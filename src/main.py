"""Pipeline orchestrator: config → collect → filter → report."""

from __future__ import annotations

import logging
import sys

from .config import load_settings, load_topics
from .collector import collect_papers
from .filter import filter_papers
from .report import generate_report


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

    if not settings.llm_api_key:
        logger.error("No API key found. Set GLM_API_KEY in your .env file.")
        sys.exit(1)

    if not topics:
        logger.error("No topics defined in config/topics.yaml")
        sys.exit(1)

    logger.info("Loaded %d topics, model=%s, categories=%s",
                len(topics),
                settings.llm_model,
                sorted(set(cat for t in topics for cat in t.categories)))

    # 2. Collect papers
    logger.info("Collecting papers from arXiv...")
    papers = collect_papers(topics, settings)
    if not papers:
        logger.warning("No papers found. Check your categories and date range.")
        sys.exit(0)

    # 3. Filter by relevance
    logger.info("Filtering papers for relevance...")
    relevant = filter_papers(papers, topics, settings)

    # 4. Generate report
    logger.info("Generating report...")
    all_categories = sorted(set(cat for t in topics for cat in t.categories))
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


if __name__ == "__main__":
    main()
