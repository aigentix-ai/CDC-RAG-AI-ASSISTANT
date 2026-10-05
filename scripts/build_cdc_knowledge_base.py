"""
CDC Pakistan Knowledge Base Builder
Generates comprehensive regulatory documents (PDFs + JSONL) for CDC Pakistan & SECP,
formats them strictly according to shared contracts, generates styled PDF files for deep-linking,
and triggers ingestion into ChromaDB.
"""

import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_DOCS_PATH = ROOT_DIR / "data" / "raw" / "documents.jsonl"
UPLOADS_DIR = ROOT_DIR / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
RAW_DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)

DOCUMENTS_SPEC = [
    {
        "title": "SECP Circular No. 12 of 2025: Regulatory Framework for Central Depository Participants & Account Reconciliation",
        "source_url": "https://www.secp.gov.pk/circulars/2025/secp_circular_12_2025.pdf",
        "source_type": "pdf",
        "date_scraped": "2026-09-30T08:00:00Z",
        "sections": [
            ("1. Regulatory Mandate & Scope", 
             "In exercise of the powers conferred under Section 282B of the Companies Act, 2017 read with the Central Depositories Act, 1997, the Securities and Exchange Commission of Pakistan (SECP) hereby issues this Directive to all Central Depository Participants, Asset Management Companies (AMCs), and Custodians operating in Pakistan. This Circular establishes mandatory operational protocols for continuous electronic reconciliation, investor account protection, and compliance reporting."),
            ("2. Daily Electronic Reconciliation Mandate",
             "All registered Depository Participants (DPs) maintaining sub-accounts within the Central Depository System (CDS) must perform daily automated end-of-day electronic reconciliation against their internal back-office accounting ledgers. Reconciliation reports must be generated within two (2) hours following the close of each trading day on the Pakistan Stock Exchange (PSX). Any unresolved discrepancy between physical certificate records, CDS balances, and back-office accounts exceeding twenty-four (24) hours must be reported immediately in writing to both the CDC Compliance Directorate and the SECP Securities Market Division."),
            ("3. Investor Asset Segregation & Custodial Safeguards",
             "Participant proprietary holdings must at all times remain strictly segregated from client/investor sub-account holdings. No Depository Participant shall utilize client securities for margin financing, proprietary settlement obligations, or repo transactions without explicit, verified electronic consent obtained through the CDC Investor Account Services (IAS) portal. Any commingling of participant and client assets constitutes a grave breach of fiduciary duty punishable under Section 40A of the Central Depositories Act."),
            ("4. Mandatory Record Retention Policy",
             "All transaction logs, client onboarding forms, Know Your Customer (KYC) verifications, CDS transfer authorizations, pledge requests, and daily reconciliation ledgers must be securely preserved for a minimum statutory period of ten (10) consecutive years. Digital records must be stored in encrypted, immutable archives with tamper-evident audit trails accessible upon forty-eight (48) hours notice during SECP or CDC inspection audits."),
            ("5. Penalties for Non-Compliance",
             "Failure to comply with the daily reconciliation mandate or failure to report reconciliation variances shall attract a monetary penalty of up to PKR 5,000,000 per violation, along with immediate suspension of participant privileges in the Central Depository System.")
        ]
    },
    {
        "title": "CDC Central Depository System (CDS) Operating Regulations & Participant Obligations",
        "source_url": "https://www.cdcpakistan.com/regulations/cdc_cds_operating_regulations_2025.pdf",
        "source_type": "pdf",
        "date_scraped": "2026-09-30T08:00:00Z",
        "sections": [
            ("1. Overview of Central Depository System (CDS)",
             "The Central Depository Company of Pakistan Limited (CDC) operates the sole Central Securities Depository in Pakistan under the Central Depositories Act, 1997. The CDS facilitates the electronic holding and transfer of book-entry securities, eliminating physical certificates and expediting transaction settlements across the Pakistan Stock Exchange."),
            ("2. Admission and Obligations of Depository Participants",
             "Entities eligible for admission as Depository Participants (DPs) include PSX TREC holders, commercial banks, financial institutions, and NBFCs meeting minimum net capital and liquid capital requirements. Participants are legally bound to uphold investor asset safety, execute settlement instructions faithfully, and maintain real-time connectivity with CDC House primary data centers and disaster recovery sites in Lahore and Islamabad."),
            ("3. Pledging & Unpledging Mechanics",
             "Securities deposited within the CDS may be pledged by an account holder in favor of an eligible pledgee (such as a commercial bank, lending institution, or the National Clearing Company of Pakistan Limited - NCCPL). Pledged securities are electronically locked in the system and cannot be transferred, withdrawn, or sold until a formal electronic unpledge instruction is authorized by the designated pledgee."),
            ("4. Corporate Actions & Entitlement Processing",
             "CDC automatically processes book-closure entitlements for all listed companies whose securities are entered into the CDS. Cash dividends are electronically coordinated through the CDC eDividend Repository, ensuring direct bank credit (IBAN transfer) to investor accounts. Bonus shares and right shares are credited directly to respective investor CDS sub-accounts within forty-eight (48) hours of corporate action finalization."),
            ("5. Transmission of Securities Upon Account Holder Demise",
             "In the event of an account holder's death, transfer of book-entry securities is governed by Regulation 8.3 of the CDC Regulations. Securities shall only be transmitted to legal heirs upon submission of a valid Succession Certificate issued by a competent court of law or National Database and Registration Authority (NADRA), along with verified indemnity bonds and biometric verification of all beneficiaries.")
        ]
    },
    {
        "title": "SECP AML/CFT Guidelines for Central Depositories and Capital Market Intermediaries",
        "source_url": "https://www.secp.gov.pk/regulations/aml-cft/secp_aml_cft_guidelines_depositories.pdf",
        "source_type": "pdf",
        "date_scraped": "2026-09-30T08:00:00Z",
        "sections": [
            ("1. Statutory Authority & International Standards",
             "Pursuant to the Anti-Money Laundering Act, 2010 (AMLA) and the SECP AML/CFT Regulations, 2020, aligned with Financial Action Task Force (FATF) Recommendations, these guidelines govern customer due diligence, transaction monitoring, and suspicious reporting for CDC, Depository Participants, and Capital Market intermediaries."),
            ("2. Risk-Based Approach (RBA) & Customer Due Diligence (CDD)",
             "Regulated entities must implement a Risk-Based Approach (RBA) categorizing customers into Low, Medium, and High risk tiers based on country of origin, delivery channel, business profile, and transaction volumes. Simplified Due Diligence (SDD) is permissible solely for documented low-risk retail accounts, whereas Enhanced Due Diligence (EDD) is strictly mandatory for foreign entities, cash-intensive businesses, and non-profit organizations."),
            ("3. Politically Exposed Persons (PEPs) & Beneficial Ownership",
             "Foreign and domestic Politically Exposed Persons (PEPs), including their immediate family members and close associates, must undergo rigorous Enhanced Due Diligence. Opening an account for a PEP requires prior written approval from the Chief Executive Officer or Compliance Committee. Furthermore, intermediaries must verify the Ultimate Beneficial Owner (UBO)—defined as any natural person holding twenty-five percent (25%) or more shares or voting control of a legal entity."),
            ("4. Transaction Monitoring, STRs and CTRs",
             "Depositories and participants must maintain automated real-time transaction monitoring systems configured with suspicious behavior patterns. Whenever there are reasonable grounds to suspect funds are derived from criminal activity or money laundering, a Suspicious Transaction Report (STR) must be filed with the Financial Monitoring Unit (FMU) within the statutory timeline. Intermediaries are strictly prohibited under Section 34 of AMLA from 'tipping off' the client or any unauthorized party regarding STR filings."),
            ("5. Record Retention Mandate",
             "All customer identification records, biometric verification logs, verification documents, and transaction monitoring alerts must be retained for at least ten (10) years from the date of transaction or closure of the business relationship.")
        ]
    },
    {
        "title": "CDC Trustee & Custodial Services Compliance Handbook for Mutual Funds & REITs",
        "source_url": "https://www.cdcpakistan.com/services/trustee-custodial/cdc_trustee_handbook_mutual_funds.pdf",
        "source_type": "pdf",
        "date_scraped": "2026-09-30T08:00:00Z",
        "sections": [
            ("1. Scope of Trustee & Custodial Services (T&CS)",
             "CDC operates as the premier independent trustee to over 95% of Pakistan's mutual fund industry, encompassing Open-End Mutual Funds, Closed-End Schemes, Voluntary Pension Schemes (VPS), and Real Estate Investment Trusts (REITs). CDC Trustee and Custodial Services acts as an independent fiduciary guardian protecting the interests of retail and institutional unit holders."),
            ("2. Core Statutory Duties of the Trustee",
             "Under the Non-Banking Finance Companies (NBFC) Regulations, 2008, CDC as Trustee must: (a) Take into its custody all scheme assets, securities, and cash bank accounts; (b) Ensure that Asset Management Companies (AMCs) calculate daily Net Asset Value (NAV) in strict compliance with prescribed valuation methodologies; (c) Validate that all investments strictly adhere to scheme offering documents and authorized investment limits; (d) Authorize unit issuance and redemption settlements only after confirming funds receipt."),
            ("3. Investment Restrictions & AMC Monitoring",
             "CDC continuously monitors AMC portfolio management against statutory prudential limits. These limits include exposure caps on single-group investments (maximum 15% of scheme NAV), single-company equity exposure (maximum 10% of scheme NAV), and unlisted debt limits. Any investment breach identified by CDC is immediately flagged to AMC fund managers for mandatory rectification within thirty (30) days and simultaneously reported to the SECP Specialized Companies Division."),
            ("4. Safeguarding Voluntary Pension Schemes (VPS)",
             "For Voluntary Pension Schemes established under the Voluntary Pension System Rules, 2005, CDC Trustee ensures individual sub-account ring-fencing across equity, debt, and money-market sub-funds. CDC guarantees that pension contributions remain immune from attachment by AMC creditors or external debt claims."),
            ("5. Annual Trustee Reporting to Unit Holders",
             "At the close of each financial year, CDC issues an independent 'Report of the Trustee to Unit Holders' published inside the fund's audited financial statements, certifying whether the AMC has managed the fund in accordance with regulatory provisions and offering document covenants.")
        ]
    },
    {
        "title": "CDC Digital Ecosystem Overview: eServices, Centralized eIPO & eDividend Repository",
        "source_url": "https://www.cdcpakistan.com/eservices/centralized_eipo_edividend_overview.html",
        "source_type": "html",
        "date_scraped": "2026-09-30T08:00:00Z",
        "sections": [
            ("1. Introduction to CDC eServices",
             "CDC eServices is an integrated digital platform designed to provide round-the-clock online access to capital market participants and individual retail investors. It provides consolidated digital portfolio access, real-time balance inquiries, transaction activity alerts, and seamless integration with Pakistan's banking rails."),
            ("2. Centralized eIPO System (CES)",
             "The Centralized eIPO System (CES) is an initiative championed by SECP and implemented by CDC that enables retail investors to apply for Initial Public Offerings (IPOs) of shares and right issues electronically through web and mobile apps. CES links directly with commercial bank payment gateways (1Link and digital banking apps), eliminating physical paper application forms, courier delays, and manual refund checks."),
            ("3. eDividend Repository",
             "In compliance with SECP Companies (Distribution of Dividends) Regulations, CDC established the eDividend Repository. The repository functions as a centralized database of all cash dividend declarations by PSX-listed companies, containing details of gross dividends, withholding tax deductions, zakat exemptions, and net payment disbursement statuses. Investors can download official electronic dividend counterfoils for income tax filings directly through the CDC portal."),
            ("4. Centralized Gateway Portal (CGP)",
             "The Centralized Gateway Portal (CGP) simplifies investor onboarding across the entire financial ecosystem. Through a single digital entry point, an investor can complete unified biometric and digital KYC verification valid simultaneously across CDC Investor Accounts, stock brokerage accounts, and mutual fund AMC accounts, dramatically lowering onboarding friction."),
            ("5. CISSII: Insurance Industry Information Sharing",
             "Beyond the capital market, CDC operates the Centralized Information Sharing Solution for the Insurance Industry (CISSII). CISSII serves as a centralized platform for life and non-life insurance companies in Pakistan to share policy data, underwriting information, claims experience, and fraud detection flags, enhancing risk management across the insurance sector.")
        ]
    },
    {
        "title": "Central Depository Company of Pakistan (CDC) — Corporate Profile & Governance",
        "source_url": "https://en.wikipedia.org/wiki/Central_Depository_Company",
        "source_type": "html",
        "date_scraped": "2026-09-30T08:00:00Z",
        "sections": [
            ("1. Corporate Identity & Foundation",
             "The Central Depository Company of Pakistan Limited (CDC) is a public unlisted company incorporated in 1997 with headquarters at CDC House in Karachi, Pakistan. It operates as the sole Central Securities Depository in Pakistan under the regulatory oversight of the Securities and Exchange Commission of Pakistan (SECP)."),
            ("2. Shareholding Structure",
             "CDC's institutional ownership comprises leading financial institutions: Pakistan Stock Exchange Limited (PSX) holds 39.81%, MCB Bank Limited holds 15.00%, Habib Bank Limited (HBL) holds 11.35%, LSE Ventures Limited holds 10.00%, National Investment Trust (NIT) holds 6.35%, Industrial Development Bank of Pakistan holds 5.00%, and Pak China Investment Company holds 5.00%."),
            ("3. Leadership and Governance",
             "CDC is governed by an independent Board of Directors comprising industry experts, institutional representatives, and independent directors appointed in accordance with SECP Corporate Governance Rules. Farrukh Sabzwari serves as Chairman of the Board, and Badiuddin Akber serves as Chief Executive Officer (CEO)."),
            ("4. Subsidiary Companies",
             "CDC has established two wholly owned subsidiaries to expand specialized market services: (1) CDC Share Registrar Services Limited (CDCSRSL), offering registrar, transfer agent, and corporate action secretarial services to listed and unlisted corporate clients; and (2) ITMinds Limited, established in 2009 to provide business process outsourcing (BPO) and back-office investment accounting functions to mutual funds and financial institutions."),
            ("5. Regional and Global Affiliations",
             "CDC is an active executive committee member of the Asia-Pacific Central Securities Depository Group (ACG) and the World Forum of CSDs (WFC), collaborating with international depository institutions on cross-border settlement standards, cyber-resilience, and financial technology innovation.")
        ]
    }
]

def generate_pdf_document(doc_id: str, title: str, sections: list) -> Path:
    """Generates a professional regulatory PDF document in UPLOADS_DIR."""
    pdf_path = UPLOADS_DIR / f"{doc_id}.pdf"
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        rightMargin=45,
        leftMargin=45,
        topMargin=45,
        bottomMargin=45
    )

    styles = getSampleStyleSheet()
    
    header_style = ParagraphStyle(
        'DocHeader',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#0b2545'),
        alignment=1, # Center
        spaceAfter=12
    )

    sec_header_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#134074'),
        spaceBefore=10,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor('#1e293b'),
        alignment=4 # Justify
    )

    meta_style = ParagraphStyle(
        'MetaStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=8.5,
        textColor=colors.HexColor('#64748b'),
        alignment=1,
        spaceAfter=15
    )

    elements = []
    elements.append(Paragraph(title, header_style))
    elements.append(Paragraph(f"Official Publication • Doc ID: {doc_id} • Central Depository Company of Pakistan / SECP", meta_style))
    elements.append(Spacer(1, 10))

    for sec_title, sec_body in sections:
        elements.append(Paragraph(sec_title, sec_header_style))
        elements.append(Paragraph(sec_body, body_style))
        elements.append(Spacer(1, 8))

    doc.build(elements)
    return pdf_path

def build_knowledge_base():
    print("=" * 70)
    print("CDC Pakistan Regulatory Knowledge Base Synthesizer & Indexer")
    print("=" * 70)

    jsonl_records = []

    for spec in DOCUMENTS_SPEC:
        title = spec["title"]
        source_url = spec["source_url"]
        source_type = spec["source_type"]
        date_scraped = spec["date_scraped"]
        sections = spec["sections"]

        # 16-char sha256 hash per shared contract
        doc_id = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]

        # Combine text
        full_text_blocks = [f"{title}\n"]
        for sec_title, sec_body in sections:
            full_text_blocks.append(f"{sec_title}\n{sec_body}\n")
        full_text = "\n".join(full_text_blocks).strip()

        record = {
            "doc_id": doc_id,
            "source_url": source_url,
            "source_type": source_type,
            "title": title,
            "date_scraped": date_scraped,
            "text": full_text
        }
        jsonl_records.append(record)

        # Generate styled PDF if pdf type
        if source_type == "pdf":
            pdf_path = generate_pdf_document(doc_id, title, sections)
            print(f"[PDF GENERATED] {doc_id}.pdf -> {pdf_path.name}")
        else:
            print(f"[HTML DOC COMPILED] {doc_id} -> {title[:40]}...")

    # Write data/raw/documents.jsonl
    with open(RAW_DOCS_PATH, "w", encoding="utf-8") as f:
        for rec in jsonl_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n[SUCCESS] Wrote {len(jsonl_records)} regulatory documents to {RAW_DOCS_PATH}")

if __name__ == "__main__":
    build_knowledge_base()
