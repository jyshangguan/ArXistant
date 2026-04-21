"""Pipeline orchestrator: config → DB → tree → collect → analyze → report."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import load_settings, load_topics, PROJECT_ROOT
from .collector import collect_papers, RawPaper
from .storage import (
    init_db, insert_papers_batch, get_unanalyzed_papers,
    update_paper_analysis, upsert_paper_tree_link,
    insert_candidate, get_tree_node_by_name,
    write_candidates_yaml, process_candidate_review,
)
from .tree import import_tree_from_yaml, format_tree_for_prompt
from .analyze import analyze_papers
from .filter import filter_papers
from .report import generate_report, generate_tree_report


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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    # 1. Load config
    logger.info("Loading configuration...")
    topics = load_topics()
    settings = load_settings()

    if not topics:
        logger.error("No topics defined in config/topics.yaml")
        sys.exit(1)

    if not settings.llm_api_key:
        logger.error("No API key found. Set GLM_API_KEY in your .env file.")
        sys.exit(1)

    all_categories = sorted(set(cat for t in topics for cat in t.categories))
    logger.info("Loaded %d topics, model=%s, categories=%s",
                len(topics),
                settings.llm_model,
                all_categories)

    # 2. Decide pipeline mode: tree-aware or legacy
    use_tree = bool(settings.db_path)

    if use_tree:
        _run_tree_pipeline(logger, topics, settings, all_categories)
    else:
        _run_legacy_pipeline(logger, topics, settings, all_categories)


def _run_legacy_pipeline(logger, topics, settings, all_categories) -> None:
    """Original filter-based pipeline (no DB, no tree)."""
    logger.info("Running legacy pipeline (no database)")

    papers = collect_papers(topics, settings)
    if not papers:
        logger.warning("No papers found. Check your categories and date range.")
        sys.exit(0)

    logger.info("Filtering papers for relevance...")
    relevant = filter_papers(papers, topics, settings)

    logger.info("Generating report...")
    report_path = generate_report(
        relevant=relevant,
        total_scanned=len(papers),
        topics=topics,
        all_categories=all_categories,
        settings=settings,
    )

    logger.info("Done. Report: %s", report_path)
    print(f"\nReport saved to: {report_path}")
    print(f"Papers scanned: {len(papers)}")
    print(f"Relevant papers: {len(relevant)}")


def _run_tree_pipeline(logger, topics, settings, all_categories) -> None:
    """New tree-aware pipeline with SQLite storage."""
    # 3. Initialize database
    logger.info("Initializing database at %s", settings.db_path)
    conn = init_db(settings.db_path)

    # 4. Import knowledge tree from YAML if first run
    tree_yaml = PROJECT_ROOT / "config" / "knowledge_tree.yaml"
    if tree_yaml.exists():
        imported = import_tree_from_yaml(conn, tree_yaml)
        if imported:
            logger.info("Imported %d tree nodes from %s", imported, tree_yaml)

    # 5. Process candidate reviews from YAML
    candidates_file = Path(settings.candidates_path)
    if candidates_file.exists():
        stats = process_candidate_review(conn, candidates_file)
        if stats["confirmed"] + stats["rejected"] > 0:
            logger.info("Candidate review: %d confirmed, %d rejected",
                        stats["confirmed"], stats["rejected"])

    # 6. Collect papers
    logger.info("Collecting papers from arXiv...")
    papers = collect_papers(topics, settings)
    if not papers:
        logger.warning("No papers found. Check your categories and date range.")

    # 7. Store papers in DB
    if papers:
        new_count = insert_papers_batch(conn, [_raw_paper_to_dict(p) for p in papers])
        logger.info("Stored %d new papers (%d total in DB)", new_count,
                     sum(1 for _ in papers))

    # 8. Analyze unanalyzed papers
    unanalyzed = get_unanalyzed_papers(conn)
    analysis_results = []
    if unanalyzed:
        logger.info("Analyzing %d unanalyzed papers...", len(unanalyzed))

        # Format tree for LLM prompt
        tree_prompt = format_tree_for_prompt(conn)

        # Convert StoredPaper back to RawPaper for the analyzer
        from datetime import datetime, timezone
        raw_unanalyzed = []
        for sp in unanalyzed:
            raw_unanalyzed.append(RawPaper(
                arxiv_id=sp.arxiv_id,
                title=sp.title,
                authors=sp.authors.split("\n") if sp.authors else [],
                abstract=sp.abstract,
                published=datetime.fromisoformat(sp.published),
                categories=sp.categories.split(",") if sp.categories else [],
                primary_category=sp.primary_category,
                pdf_url=sp.pdf_url,
                entry_url=sp.entry_url,
            ))

        analysis_results = analyze_papers(raw_unanalyzed, tree_prompt, settings)

        # 9. Store analysis results
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
    else:
        logger.info("All papers already analyzed, skipping analysis")

    # 10. Write candidates file for user review
    write_candidates_yaml(conn, settings.candidates_path)
    logger.info("Candidates written to %s", settings.candidates_path)

    # 11. Generate tree-aware report
    logger.info("Generating tree-aware report...")
    report_path = generate_tree_report(
        conn=conn,
        total_scanned=len(papers) if papers else 0,
        topics=topics,
        all_categories=all_categories,
        settings=settings,
    )

    conn.close()

    logger.info("Done. Report: %s", report_path)
    print(f"\nReport saved to: {report_path}")
    print(f"Papers scanned this run: {len(papers) if papers else 0}")
    print(f"Papers analyzed this run: {len(analysis_results)}")
    print(f"Candidates pending review: {settings.candidates_path}")


if __name__ == "__main__":
    main()
