"""
Unit and integration tests for Dynamic Category Discovery & Taxonomy Extraction.
Ensures categories are extracted from real URLs, sitemaps, breadcrumbs, and context,
without hardcoding fixed category lists or static seeds.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "backend"))

from crawler_engine import (
    clean_category_slug,
    clean_sitemap_hint,
    extract_category_from_url_and_context,
    classify_regulatory_category,
    discover_categorized_links,
)


def test_clean_category_slug_and_acronyms():
    """Verify acronyms are properly preserved/capitalized and formatted."""
    assert clean_category_slug("aml-cft") == "AML / CFT"
    assert clean_category_slug("kyc-cdd-guidelines") == "KYC / CDD Guidelines"
    assert clean_category_slug("cds-regulations") == "CDS Regulations"
    assert clean_category_slug("cdc-notices") == "CDC Notices"
    assert clean_category_slug("secp-directives") == "SECP Directives"
    assert clean_category_slug("eipo-portal") == "eIPO Portal"
    assert clean_category_slug("edividend-gateway") == "eDividend Gateway"
    assert clean_category_slug("eservices-guide") == "eServices Guide"
    assert clean_category_slug("trustee-custodial-services") == "Trustee & Custodial Services"
    assert clean_category_slug("reit-framework") == "REIT Framework"
    assert clean_category_slug("vps-regulations") == "VPS Regulations"
    assert clean_category_slug("circulars-and-notices") == "Circulars & Notices"


def test_clean_sitemap_hint():
    """Verify sitemap filenames yield clean category hints."""
    assert clean_sitemap_hint("regulation-sitemap.xml") == "Regulation"
    assert clean_sitemap_hint("wp-sitemap-posts-regulation-1.xml") == "Posts Regulation"
    assert clean_sitemap_hint("circulars-sitemap.xml") == "Circulars"
    assert clean_sitemap_hint("compliance-aml-sitemap.xml") == "Compliance AML"
    assert clean_sitemap_hint("services_sitemap.xml") == "Services"


def test_extract_category_from_url_and_context():
    """Verify dynamic category derivation from URL paths and titles."""
    # Two-level taxonomy
    assert extract_category_from_url_and_context(
        "https://cdcpakistan.com/regulations/circulars-and-notices/"
    ) == "Regulations / Circulars & Notices"

    assert extract_category_from_url_and_context(
        "https://cdcpakistan.com/compliance/aml-cft/"
    ) == "Compliance / AML / CFT"

    assert extract_category_from_url_and_context(
        "https://cdcpakistan.com/services/eipo-portal/"
    ) == "Services / eIPO Portal"

    # Single-level taxonomy
    assert extract_category_from_url_and_context(
        "https://cdcpakistan.com/investor-education/"
    ) == "Investor Education"

    # File path with parent category
    assert extract_category_from_url_and_context(
        "https://cdcpakistan.com/files/circulars/2024/notice-01.pdf"
    ) == "Circulars / 2024"

    # Sitemap hint overrides generic URL
    assert extract_category_from_url_and_context(
        "https://example.com/item123",
        sitemap_hint="regulation-sitemap.xml"
    ) == "Regulation"

    # Title fallback when path is root
    assert extract_category_from_url_and_context(
        "https://cdcpakistan.com/",
        title="SECP Directives & CDC Circular Notices"
    ) == "Circulars & Directives"


def test_discover_categorized_links_with_urlset_sitemap():
    """Verify discover_categorized_links dynamically categorizes links from XML sitemaps."""
    mock_sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
       <url>
          <loc>https://cdcpakistan.com/regulations/cdc-regulations/</loc>
       </url>
       <url>
          <loc>https://cdcpakistan.com/compliance/aml-cft/</loc>
       </url>
       <url>
          <loc>https://cdcpakistan.com/services/eipo-portal/</loc>
       </url>
       <url>
          <loc>https://cdcpakistan.com/downloads/forms/account-form.pdf</loc>
       </url>
    </urlset>"""

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_sitemap_xml
        mock_resp.content = mock_sitemap_xml.encode("utf-8")
        mock_get.return_value = mock_resp

        res = discover_categorized_links("https://cdcpakistan.com/sitemap.xml", max_links=10)

        assert res["total_discovered"] == 4
        categories = res["categories"]

        # Ensure no static 6-category requirement; categories are dynamic
        assert any("Regulation" in cat or "CDS" in cat for cat in categories)
        assert any("Compliance" in cat or "AML" in cat for cat in categories)
        assert any("Services" in cat or "eIPO" in cat for cat in categories)
        assert any("Downloads" in cat or "Forms" in cat for cat in categories)


def test_discover_categorized_links_with_sitemap_index():
    """Verify discover_categorized_links navigates sitemap indexes with sub-sitemaps."""
    mock_index_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
       <sitemap>
          <loc>https://cdcpakistan.com/wp-sitemap-posts-regulations-1.xml</loc>
       </sitemap>
    </sitemapindex>"""

    mock_sub_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
       <url>
          <loc>https://cdcpakistan.com/regulations/statutory-rules/</loc>
       </url>
       <url>
          <loc>https://cdcpakistan.com/regulations/procedures/</loc>
       </url>
    </urlset>"""

    def mock_fetch(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "index" in url or "sitemap.xml" in url:
            resp.text = mock_index_xml
            resp.content = mock_index_xml.encode("utf-8")
        else:
            resp.text = mock_sub_xml
            resp.content = mock_sub_xml.encode("utf-8")
        return resp

    with patch("requests.get", side_effect=mock_fetch):
        res = discover_categorized_links("https://cdcpakistan.com/sitemap_index.xml", max_links=10)
        assert res["total_discovered"] == 2
        categories = res["categories"]
        assert len(categories) >= 1
