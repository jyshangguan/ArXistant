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
    """Return 12 papers (2 batches × batch_size=6) for integration-style tests."""
    papers = [sample_paper]
    extra = [
        ("2504.67890", "GRB 2504A: A Nearby Long Gamma-Ray Burst with Late-time Radio Emission",
         ["Eve Zhang", "Frank Li"], "astro-ph.HE",
         "We report observations of GRB 2504A, a nearby long-duration gamma-ray burst detected by Swift. Radio observations at 5 and 8 GHz reveal a late-time rebrightening, suggesting energy injection from a central engine."),
        ("2504.11111", "Tidal Disruption Events in Nearby Galaxies: A Statistical Study",
         ["Grace Chen", "Henry Wu", "Ivan Petrov"], "astro-ph.HE",
         "We present a systematic survey of TDE candidates in galaxies within 50 Mpc using optical spectroscopy from SDSS and X-ray data from eROSITA."),
        ("2504.22222", "Bar-driven Secular Evolution in Disk Galaxies",
         ["Julia Martinez", "Kevin Park"], "astro-ph.GA",
         "Using N-body simulations we show that bar-driven perturbations can drive significant gas inflows to the central kiloparsec of disk galaxies."),
        ("2504.33333", "Spiral Arm Pattern Speeds in the Milky Way Using Gaia DR4",
         ["Liam O'Brien", "Maya Singh", "Noah Tanaka", "Olivia Wang"], "astro-ph.GA",
         "We measure pattern speeds of the Milky Way spiral arms using Gaia DR4 proper motions of young stars. Our results favor a two-arm spiral with a pattern speed of 28 km/s/kpc."),
        ("2504.44444", "Fast Radio Burst 202404A: Localization and Host Galaxy Properties",
         ["Paul Kim", "Rosa Liu"], "astro-ph.HE",
         "We report the localization of FRB 202404A to a star-forming spiral galaxy at z=0.12. The host galaxy has a stellar mass of 10^10 solar masses and a star formation rate of 3 M_sun/yr."),
        ("2504.55555", "Molecular Gas Kinematics in the Central Molecular Zone",
         ["Quinn Davis", "Rachel Edwards", "Sam Foster", "Tina Garcia"], "astro-ph.GA",
         "ALMA observations of the Central Molecular Zone reveal non-circular gas motions consistent with a combination of bar-driven streaming and gravitational torques from the nuclear stellar disk."),
        ("2504.66666", "Type Ia Supernova Rates in Galaxy Clusters at z < 0.1",
         ["Uma Patel"], "astro-ph.HE",
         "We measure the Type Ia supernova rate in low-redshift galaxy clusters using a 10-year survey from the Zwicky Transient Facility. The rate scales with cluster richness."),
        ("2504.77777", "Vertical Oscillations of the Milky Way Disk Using K Giants",
         ["Victor Huang", "Wendy Zhao", "Xavier Brown"], "astro-ph.GA",
         "Analysis of LAMOST K giants reveals vertical oscillations of the Milky Way disk with an amplitude of 0.3 kpc, consistent with perturbations from the Sagittarius dwarf galaxy."),
        ("2504.88888", "Polarization of Gamma-Ray Burst Afterglows: Evidence for Ordered Magnetic Fields",
         ["Yuki Sato", "Zara Ahmed"], "astro-ph.HE",
         "Polarimetric observations of GRB afterglows with ALMA reveal high linear polarization fractions, suggesting ordered magnetic fields in the jet emission region."),
        ("2504.99999", "The Dark Matter Halo of M31 from Rotational Curve Modeling",
         ["Brian Lee", "Clara Nguyen", "David Schmidt", "Emily Taylor"], "astro-ph.GA",
         "We fit the rotation curve of M31 using a multi-component model including a NFW dark matter halo. The best-fit halo concentration is c=12, consistent with LCDM predictions."),
        ("2504.00001", "Coronal Mass Ejection-driven Shock Acceleration of Solar Energetic Particles",
         ["Frank Miller", "Grace Nakamura"], "astro-ph.HE",
         "We model the acceleration of solar energetic particles at CME-driven shocks using a diffusive shock acceleration framework and compare predictions with Parker Solar Probe observations."),
    ]
    for i, (aid, title, authors, cat, abstract) in enumerate(extra):
        papers.append(RawPaper(
            arxiv_id=aid,
            title=title,
            authors=authors,
            abstract=abstract,
            published=datetime(2025, 4, 19 - i % 3, tzinfo=timezone.utc),
            categories=[cat],
            primary_category=cat,
            pdf_url=f"https://arxiv.org/pdf/{aid}",
            entry_url=f"https://arxiv.org/abs/{aid}",
        ))
    return papers


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
        max_text_chars=80000,
        html_timeout=30,
    )


@pytest.fixture
def bot_settings():
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
        max_text_chars=80000,
        html_timeout=30,
        feishu_app_id="test_app_id",
        feishu_app_secret="test_secret",
        feishu_verification_token="test_token",
        feishu_encrypt_key="",
        feishu_bot_name="ArXistant",
        bot_host="0.0.0.0",
        bot_port=8000,
        webhook_path="/feishu/webhook",
        target_chat_id="test_chat_id",
        session_max_messages=20,
        report_cron="0 9 * * *",
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


@pytest.fixture
def sample_html():
    """Sample arXiv HTML for testing the HTML parser."""
    return """<!DOCTYPE html>
<html>
<head>
  <title>Test Paper</title>
  <base href="/html/2504.12345v1/">
</head>
<body>
  <header class="ltx_page_header">
    <h1 class="ltx_title ltx_title_document">Test Paper Title</h1>
  </header>
  <div class="ltx_abstract">
    <h2 class="ltx_title ltx_title_abstract">Abstract</h2>
    <p>This is a test abstract for testing purposes.</p>
  </div>
  <section class="ltx_section">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1</span> Introduction</h2>
    <p>Introduction text here.</p>
  </section>
  <section class="ltx_bibliography">
    <h2 class="ltx_title ltx_title_section">References</h2>
    <p>[1] Reference here.</p>
  </section>
</body>
</html>
"""
