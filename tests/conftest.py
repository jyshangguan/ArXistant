"""Shared fixtures for ArXistant tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import sqlite3

from src.config import Settings, Topic
from src.collector import RawPaper
from src.storage import init_db


@pytest.fixture
def sample_topic():
    return Topic(
        name="Galactic Dynamics",
        description="Dynamics and structure of the Milky Way and other galaxies.",
        keywords=["galactic dynamics", "Milky Way", "spiral arms"],
        categories=["astro-ph.GA"],
    )


@pytest.fixture
def sample_topics(sample_topic):
    return [
        sample_topic,
        Topic(
            name="High-Energy Transients",
            description="Gamma-ray bursts, supernovae, and tidal disruption events.",
            keywords=["gamma-ray burst", "supernova", "tidal disruption event"],
            categories=["astro-ph.HE"],
        ),
    ]


@pytest.fixture
def sample_paper():
    return RawPaper(
        arxiv_id="2504.12345",
        title="Dynamics of Barred Spiral Galaxies in the Local Universe",
        authors=["Alice Smith", "Bob Jones", "Carol White", "Dave Black"],
        abstract="We present a comprehensive study of barred spiral galaxies using data from the SDSS and Gaia surveys. Our sample includes over 5000 galaxies within 100 Mpc, and we analyze the bar fraction as a function of stellar mass and environment.",
        published=datetime(2025, 4, 20, tzinfo=timezone.utc),
        categories=["astro-ph.GA"],
        primary_category="astro-ph.GA",
        pdf_url="https://arxiv.org/pdf/2504.12345",
        entry_url="https://arxiv.org/abs/2504.12345",
    )


@pytest.fixture
def sample_papers(sample_paper):
    paper2 = RawPaper(
        arxiv_id="2504.67890",
        title="GRB 2504A: A Nearby Long Gamma-Ray Burst with Late-time Radio Emission",
        authors=["Eve Zhang", "Frank Li"],
        abstract="We report observations of GRB 2504A, a nearby long-duration gamma-ray burst detected by Swift. Radio observations at 5 and 8 GHz reveal a late-time rebrightening, suggesting energy injection from a central engine.",
        published=datetime(2025, 4, 19, tzinfo=timezone.utc),
        categories=["astro-ph.HE"],
        primary_category="astro-ph.HE",
        pdf_url="https://arxiv.org/pdf/2504.67890",
        entry_url="https://arxiv.org/abs/2504.67890",
    )
    return [sample_paper, paper2]


@pytest.fixture
def sample_settings():
    return Settings(
        llm_provider="openai_compatible",
        llm_model="glm-4-flash",
        llm_base_url="https://open.bigmodel.cn/api/paas/v4",
        llm_api_key="test-key-123",
        llm_temperature=0.1,
        max_results=100,
        days_back=3,
        batch_size=6,
        relevance_threshold=4,
        report_output_dir="reports",
        db_path="data/arxistant.db",
        candidates_path="data/candidates.yaml",
    )


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database with full schema."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def db_conn_with_tree(db_conn):
    """Create an in-memory DB with a sample knowledge tree."""
    from src.storage import insert_tree_node

    # Root 1: Galactic Dynamics
    ga_id = insert_tree_node(db_conn, "Galactic Dynamics",
                             "Dynamics and structure of galaxies.",
                             categories="astro-ph.GA")
    # Child: Bar Formation
    bar_id = insert_tree_node(db_conn, "Bar Formation",
                              "Formation and dynamics of galactic bars.",
                              parent_id=ga_id, level=1, categories="astro-ph.GA")
    # Child: Spiral Structure
    spiral_id = insert_tree_node(db_conn, "Spiral Structure",
                                 "Density wave theory and spiral arms.",
                                 parent_id=ga_id, level=1, categories="astro-ph.GA")
    # Root 2: High-Energy Transients
    he_id = insert_tree_node(db_conn, "High-Energy Astrophysical Transients",
                             "GRBs, supernovae, TDEs, FRBs.",
                             categories="astro-ph.HE")
    # Child: GRBs
    grb_id = insert_tree_node(db_conn, "Gamma-Ray Bursts",
                              "Observations and theory of GRBs.",
                              parent_id=he_id, level=1, categories="astro-ph.HE")

    yield db_conn
