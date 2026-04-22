"""Pipeline orchestrator: config → DB → tree → collect → analyze → report."""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from .config import Settings, Topic, load_settings, load_topics, PROJECT_ROOT
from .collector import collect_papers, RawPaper
from .storage import (
    StoredPaper, init_db, insert_papers_batch, get_unanalyzed_papers,
    update_paper_analysis, upsert_paper_tree_link,
    insert_candidate, get_tree_node_by_name,
    write_candidates_yaml, process_candidate_review,
)
from .tree import import_tree_from_yaml, format_tree_for_prompt
from .analyze import analyze_papers
from .filter import filter_papers
from .report import generate_report, generate_tree_report


logger = logging.getLogger(__name__)


def _raw_paper_to_dict(p: RawPaper) -> dict:
    """Convert a RawPaper to a dict for storage."""
    return {
        "arxiv_id": p.arxiv_id,
        "title": p.title,
        "authors": "\n".join(p.authors),
        "abstract": p.abstract,
        "published": p.published.isoformat(),
        "categories": ",".join(p.categories),
        "primary_category": p.primary_category,
        "pdf_url": p.pdf_url,
        "entry_url": p.entry_url,
    }


def _stored_to_raw(sp: StoredPaper) -> RawPaper:
    """Convert a StoredPaper back to a RawPaper for the analyzer."""
    return RawPaper(
        arxiv_id=sp.arxiv_id,
        title=sp.title,
        authors=sp.authors.split("\n") if sp.authors else [],
        abstract=sp.abstract,
        published=datetime.fromisoformat(sp.published),
        categories=sp.categories.split(",") if sp.categories else [],
        primary_category=sp.primary_category,
        pdf_url=sp.pdf_url,
        entry_url=sp.entry_url,
    )


def run_collect_and_analyze(
    conn: sqlite3.Connection,
    settings: Settings,
    topics: list[Topic] | None = None,
) -> dict:
    """Collect papers from arXiv, store them, and analyze unanalyzed ones.

    If topics is not provided, derives topics from the knowledge tree in the DB
    (falling back to config/topics.yaml if the tree is empty).

    Returns a stats dict: {"papers_collected", "papers_new", "papers_analyzed"}.
    """
    from .tree import derive_topics_from_tree

    if topics is None:
        topics = derive_topics_from_tree(conn)
        if not topics:
            topics = load_topics()
    if not topics:
        logger.warning("No topics available for collection, skipping")
        return {"papers_collected": 0, "papers_new": 0, "papers_analyzed": 0}

    # Collect papers
    logger.info("Collecting papers from arXiv...")
    papers = collect_papers(topics, settings)
    papers_collected = len(papers)

    if not papers:
        logger.warning("No papers found. Check your categories and date range.")

    # Store papers in DB
    papers_new = 0
    if papers:
        papers_new = insert_papers_batch(conn, [_raw_paper_to_dict(p) for p in papers])
        logger.info("Stored %d new papers (%d total fetched)", papers_new, papers_collected)

    # Analyze unanalyzed papers
    unanalyzed = get_unanalyzed_papers(conn)
    analyzed_count = 0
    if unanalyzed:
        logger.info("Analyzing %d unanalyzed papers...", len(unanalyzed))
        tree_prompt = format_tree_for_prompt(conn)
        raw_unanalyzed = [_stored_to_raw(sp) for sp in unanalyzed]
        analysis_results = analyze_papers(raw_unanalyzed, tree_prompt, settings)

        for ar in analysis_results:
            update_paper_analysis(
                conn,
                ar.paper.arxiv_id,
                ar.quality_score,
                ar.quality_reason,
            )
            for link in ar.tree_links:
                node = get_tree_node_by_name(conn, link["node_name"])
                if node:
                    upsert_paper_tree_link(
                        conn,
                        ar.paper.arxiv_id,
                        node.id,
                        link["relevance_score"],
                        link["relevance_reason"],
                    )
            if ar.candidate_node:
                parent_name = ar.candidate_node.get("parent_node_name", "")
                parent = get_tree_node_by_name(conn, parent_name)
                if parent:
                    insert_candidate(
                        conn,
                        name=ar.candidate_node["name"],
                        description=ar.candidate_node.get("description", ""),
                        parent_id=parent.id,
                        source_paper_ids=ar.paper.arxiv_id,
                    )
                    logger.info("Proposed candidate node '%s' under '%s'",
                                ar.candidate_node["name"], parent_name)
        analyzed_count = len(analysis_results)
    else:
        logger.info("All papers already analyzed, skipping analysis")

    return {
        "papers_collected": papers_collected,
        "papers_new": papers_new,
        "papers_analyzed": analyzed_count,
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _logger = logging.getLogger(__name__)

    # 1. Load config
    _logger.info("Loading configuration...")
    topics = load_topics()
    settings = load_settings()

    if not topics:
        _logger.error("No topics defined in config/topics.yaml")
        sys.exit(1)

    if not settings.llm_api_key:
        _logger.error("No API key found. Set GLM_API_KEY in your .env file.")
        sys.exit(1)

    all_categories = sorted(set(cat for t in topics for cat in t.categories))
    _logger.info("Loaded %d topics, model=%s, categories=%s",
                 len(topics),
                 settings.llm_model,
                 all_categories)

    # 2. Decide pipeline mode: tree-aware or legacy
    use_tree = bool(settings.db_path)

    if use_tree:
        _run_tree_pipeline(_logger, topics, settings, all_categories)
    else:
        _run_legacy_pipeline(_logger, topics, settings, all_categories)


def _run_legacy_pipeline(_logger, topics, settings, all_categories) -> None:
    """Original filter-based pipeline (no DB, no tree)."""
    _logger.info("Running legacy pipeline (no database)")

    papers = collect_papers(topics, settings)
    if not papers:
        logger.warning("No papers found. Check your categories and date range.")
        sys.exit(0)

    logger.info("Filtering papers for relevance...")
    relevant = filter_papers(papers, topics, settings)

    _logger.info("Generating report...")
    report_path = generate_report(
        relevant=relevant,
        total_scanned=len(papers),
        topics=topics,
        all_categories=all_categories,
        settings=settings,
    )

    _logger.info("Done. Report: %s", report_path)
    print(f"\nReport saved to: {report_path}")
    print(f"Papers scanned: {len(papers)}")
    print(f"Relevant papers: {len(relevant)}")


def _run_tree_pipeline(_logger, topics, settings, all_categories) -> None:
    """New tree-aware pipeline with SQLite storage."""
    # 3. Initialize database
    _logger.info("Initializing database at %s", settings.db_path)
    conn = init_db(settings.db_path)

    # 4. Import knowledge tree from YAML if first run
    tree_yaml = PROJECT_ROOT / "config" / "knowledge_tree.yaml"
    if tree_yaml.exists():
        imported = import_tree_from_yaml(conn, tree_yaml)
        if imported:
            _logger.info("Imported %d tree nodes from %s", imported, tree_yaml)

    # 5. Process candidate reviews from YAML
    candidates_file = Path(settings.candidates_path)
    if candidates_file.exists():
        stats = process_candidate_review(conn, candidates_file)
        if stats["confirmed"] + stats["rejected"] > 0:
            _logger.info("Candidate review: %d confirmed, %d rejected",
                         stats["confirmed"], stats["rejected"])

    # 6. Collect and analyze papers using the reusable pipeline function
    pipeline_stats = run_collect_and_analyze(conn, settings, topics=topics)

    # 7. Write candidates file for user review
    write_candidates_yaml(conn, settings.candidates_path)
    _logger.info("Candidates written to %s", settings.candidates_path)

    # 8. Generate tree-aware report
    _logger.info("Generating tree-aware report...")
    report_path = generate_tree_report(
        conn=conn,
        total_scanned=pipeline_stats["papers_collected"],
        topics=topics,
        all_categories=all_categories,
        settings=settings,
    )

    conn.close()

    _logger.info("Done. Report: %s", report_path)
    print(f"\nReport saved to: {report_path}")
    print(f"Papers scanned this run: {pipeline_stats['papers_collected']}")
    print(f"Papers analyzed this run: {pipeline_stats['papers_analyzed']}")
    print(f"Candidates pending review: {settings.candidates_path}")


if __name__ == "__main__":
    main()
