"""Credential-minimized Hermes readiness matrix and checks.

This module is intentionally stdlib-only. It runs during deploy before project
artifacts are installed, so it must not depend on pydantic, safe_io, or any
other /opt/shift-agent module.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import types
import urllib.request
from dataclasses import dataclass, asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_HERMES_HOME = Path("/root/.hermes")
DEFAULT_HERMES_INSTALL_ROOT = Path("/usr/local/lib/hermes-agent")
DEFAULT_ENV_PATHS = (Path("/root/.hermes/.env"), Path("/opt/shift-agent/.env"))
DEFAULT_CONFIG_PATH = Path("/root/.hermes/config.yaml")
DEFAULT_BRIDGE_URL = "http://127.0.0.1:3000/health"


@dataclass(frozen=True)
class SkillRequirement:
    skill_id: str
    credential_class: str
    last_verified: str
    source_url: str
    freshness_days: int = 90
    notes: str = ""


@dataclass(frozen=True)
class ConnectorCandidate:
    name: str
    domain: str
    source_url: str
    credential_class: str
    maturity: str
    market_state: str
    auth_modes: tuple[str, ...]
    deployment_status: str
    last_verified: str
    freshness_days: int
    notes: str
    env_names: tuple[str, ...] = ()
    session_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentCapability:
    agent_id: int
    agent_name: str
    default_mode: str
    useful_no_key_mode: str
    manual_export_mode: str
    connected_mode: str
    hermes_first_skills: tuple[str, ...]
    project_skills: tuple[str, ...]
    connector_candidates: tuple[str, ...]
    credential_boundary: str
    owner_approval_required: str
    no_go_claims: str


@dataclass(frozen=True)
class CredentialRequirement:
    name: str
    credential_class: str
    notes: str = ""


@dataclass(frozen=True)
class ReadinessOptions:
    hermes_home: Path = DEFAULT_HERMES_HOME
    hermes_install_root: Path = DEFAULT_HERMES_INSTALL_ROOT
    repo_root: Path | None = None
    env_paths: tuple[Path, ...] = DEFAULT_ENV_PATHS
    config_path: Path = DEFAULT_CONFIG_PATH
    strict_foundation: bool = False
    check_bridge: bool = False
    bridge_url: str = DEFAULT_BRIDGE_URL
    validate_plugins: tuple[str, ...] = ()
    today: date | None = None


FOUNDATION_SKILLS: tuple[SkillRequirement, ...] = (
    SkillRequirement(
        skill_id="productivity/maps",
        credential_class="none/local",
        last_verified="2026-05-14",
        source_url="https://hermes-agent.nousresearch.com/docs/reference/skills-catalog",
        notes="OSM/Nominatim/OSRM no-key location substrate.",
    ),
    SkillRequirement(
        skill_id="productivity/ocr-and-documents",
        credential_class="none/local",
        last_verified="2026-05-14",
        source_url="https://hermes-agent.nousresearch.com/docs/reference/skills-catalog",
        notes="Local PDF/document extraction before cloud OCR.",
    ),
    SkillRequirement(
        skill_id="mcp/native-mcp",
        credential_class="none/local",
        last_verified="2026-05-14",
        source_url="https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/mcp/mcp-native-mcp",
        notes="Connector substrate; target MCP servers still need credentials.",
    ),
)


CONNECTOR_CANDIDATES: tuple[ConnectorCandidate, ...] = (
    ConnectorCandidate(
        name="WhatsApp linked-device session",
        domain="messaging",
        source_url="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/whatsapp",
        credential_class="session",
        maturity="official",
        market_state="stable",
        auth_modes=("session",),
        deployment_status="installed",
        last_verified="2026-05-14",
        freshness_days=90,
        notes="No bot token, but session files grant account access.",
        session_paths=("platforms/whatsapp/session",),
    ),
    ConnectorCandidate(
        name="Intuit QuickBooks Online MCP",
        domain="accounting",
        source_url="https://github.com/intuit/quickbooks-online-mcp-server",
        credential_class="oauth",
        maturity="official",
        market_state="stable",
        auth_modes=("local_oauth",),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Prefer before custom QBO API; writes need owner approval.",
        env_names=(
            "QUICKBOOKS_CLIENT_ID",
            "QUICKBOOKS_CLIENT_SECRET",
            "QUICKBOOKS_REFRESH_TOKEN",
            "QUICKBOOKS_REALM_ID",
            "QUICKBOOKS_ENVIRONMENT",
        ),
    ),
    ConnectorCandidate(
        name="Stripe MCP",
        domain="payments",
        source_url="https://docs.stripe.com/mcp",
        credential_class="write_rail",
        maturity="official",
        market_state="stable",
        auth_modes=("remote_oauth", "restricted_api_key"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=14,
        notes="Money-moving rails require restricted scopes and owner approval.",
        env_names=("STRIPE_SECRET_KEY",),
    ),
    ConnectorCandidate(
        name="Razorpay MCP",
        domain="payments",
        source_url="https://github.com/razorpay/razorpay-mcp-server",
        credential_class="write_rail",
        maturity="official",
        market_state="stable",
        auth_modes=("local_oauth", "restricted_api_key"),
        deployment_status="candidate",
        last_verified="2026-05-27",
        freshness_days=14,
        notes="India billing rail candidate; use MCP connector posture before custom API work.",
        env_names=("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"),
    ),
    ConnectorCandidate(
        name="Square MCP Server",
        domain="pos_payments",
        source_url="https://github.com/square/square-mcp-server",
        credential_class="write_rail",
        maturity="official",
        market_state="beta",
        auth_modes=("remote_oauth", "restricted_api_key"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=14,
        notes="POS/catalog/orders/inventory candidate if customer uses Square.",
        env_names=("SQUARE_ACCESS_TOKEN",),
    ),
    ConnectorCandidate(
        name="PayPal MCP Server",
        domain="payments",
        source_url="https://github.com/paypal/paypal-mcp-server",
        credential_class="write_rail",
        maturity="official",
        market_state="stable",
        auth_modes=("remote_oauth", "restricted_api_key"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=14,
        notes="Invoices/orders/refunds where PayPal is used.",
        env_names=("PAYPAL_ACCESS_TOKEN", "PAYPAL_CLIENT_ID"),
    ),
    ConnectorCandidate(
        name="Clover API/MCP candidate",
        domain="pos",
        source_url="https://docs.clover.com/dev/docs/oauth-overview",
        credential_class="oauth",
        maturity="community",
        market_state="unknown",
        auth_modes=("local_oauth",),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Triage only if customer uses Clover; audit community MCP before use.",
        env_names=("CLOVER_CLIENT_ID", "CLOVER_CLIENT_SECRET"),
    ),
    ConnectorCandidate(
        name="Toast POS API",
        domain="pos",
        source_url="https://doc.toasttab.com/doc/devguide/apiOverview.html",
        credential_class="oauth",
        maturity="vendor",
        market_state="requires_allowlist",
        auth_modes=("local_oauth",),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Customer-POS triage candidate; not a no-key path.",
        env_names=("TOAST_CLIENT_ID", "TOAST_CLIENT_SECRET"),
    ),
    ConnectorCandidate(
        name="Airtable MCP",
        domain="lightweight_data",
        source_url="https://support.airtable.com/docs/using-the-airtable-mcp-server",
        credential_class="oauth",
        maturity="official",
        market_state="tooling_may_change",
        auth_modes=("remote_oauth", "pat"),
        deployment_status="available",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Good for SKU/P&L/customer tables; permissions mirror Airtable access.",
        env_names=("AIRTABLE_API_KEY",),
    ),
    ConnectorCandidate(
        name="Notion MCP",
        domain="docs_checklists",
        source_url="https://developers.notion.com/docs/get-started-with-mcp",
        credential_class="oauth",
        maturity="official",
        market_state="stable",
        auth_modes=("remote_oauth", "pat"),
        deployment_status="available",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Good for docs/checklists, not money system of record.",
        env_names=("NOTION_API_KEY",),
    ),
    ConnectorCandidate(
        name="Google Workspace",
        domain="workspace",
        source_url="https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/productivity/productivity-google-workspace",
        credential_class="oauth",
        maturity="official",
        market_state="stable",
        auth_modes=("local_oauth",),
        deployment_status="installed",
        last_verified="2026-05-14",
        freshness_days=90,
        notes="Calendar, Sheets, Drive, Gmail, Docs connected mode.",
        env_names=("GOOGLE_APPLICATION_CREDENTIALS",),
        session_paths=("google_token.json",),
    ),
    ConnectorCandidate(
        name="DocuSign MCP Connector",
        domain="esign",
        source_url="https://www.docusign.com/blog/claude-docusign-mcp-connector-guide",
        credential_class="oauth",
        maturity="official",
        market_state="preview",
        auth_modes=("remote_oauth", "local_oauth"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=14,
        notes="E-sign candidate; production connector access may require request.",
        env_names=("DOCUSIGN_CLIENT_ID", "DOCUSIGN_CLIENT_SECRET"),
    ),
    ConnectorCandidate(
        name="Infobip MCP",
        domain="messaging",
        source_url="https://www.infobip.com/docs/mcp",
        credential_class="api_key",
        maturity="official",
        market_state="stable",
        auth_modes=("remote_oauth", "restricted_api_key"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Multi-channel fallback; not no-token.",
        env_names=("INFOBIP_API_KEY",),
    ),
    ConnectorCandidate(
        name="Pipedream MCP",
        domain="ipaas",
        source_url="https://pipedream.com/docs/connect/mcp/",
        credential_class="managed_oauth",
        maturity="vendor",
        market_state="stable",
        auth_modes=("managed_oauth",),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Broad fallback when no vendor MCP exists; adds platform dependency.",
    ),
    ConnectorCandidate(
        name="Yelp MCP",
        domain="reviews_local",
        source_url="https://github.com/Yelp/yelp-mcp",
        credential_class="api_key",
        maturity="official",
        market_state="stable",
        auth_modes=("restricted_api_key",),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Review/business intelligence; not Google Business Profile write/reply.",
        env_names=("YELP_API_KEY",),
    ),
    ConnectorCandidate(
        name="Google Maps Grounding Lite MCP",
        domain="maps_places",
        source_url="https://developers.google.com/maps/ai/grounding-lite",
        credential_class="api_key",
        maturity="official",
        market_state="stable",
        auth_modes=("restricted_api_key", "oauth"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Connected-mode places/location research; not no-key.",
        env_names=("GOOGLE_MAPS_API_KEY",),
    ),
    ConnectorCandidate(
        name="Manual QBO/POS CSV export",
        domain="manual_export",
        source_url="https://quickbooks.intuit.com/learn-support/en-us/help-articles/export-reports-lists-and-more/00/239728",
        credential_class="none/local",
        maturity="vendor",
        market_state="stable",
        auth_modes=("manual_export",),
        deployment_status="available",
        last_verified="2026-05-14",
        freshness_days=90,
        notes="Honest no-business-API mode for finance/POS analysis.",
    ),
    ConnectorCandidate(
        name="DoorDash Drive/Marketplace API",
        domain="delivery_marketplace",
        source_url="https://developer.doordash.com/",
        credential_class="oauth",
        maturity="vendor",
        market_state="requires_allowlist",
        auth_modes=("local_oauth", "managed_oauth"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Connected/custom delivery surface; partner access required.",
    ),
    ConnectorCandidate(
        name="Uber Eats Marketplace API",
        domain="delivery_marketplace",
        source_url="https://developer.uber.com/docs/eats/introduction",
        credential_class="oauth",
        maturity="vendor",
        market_state="requires_approval",
        auth_modes=("local_oauth", "managed_oauth"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Store, menu, and order APIs require written approval for many scopes.",
    ),
    ConnectorCandidate(
        name="Grubhub restaurant integration APIs",
        domain="delivery_marketplace",
        source_url="https://get.grubhub.com/products/tech-integrations/",
        credential_class="oauth",
        maturity="vendor",
        market_state="partner_program",
        auth_modes=("local_oauth", "managed_oauth"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Partner integration surface; not a public no-key path.",
    ),
    ConnectorCandidate(
        name="Bank feed/Open Banking APIs",
        domain="bank_feed",
        source_url="https://plaid.com/docs/api/products/transactions/",
        credential_class="oauth",
        maturity="vendor",
        market_state="stable",
        auth_modes=("local_oauth", "restricted_api_key"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Bank transaction sync requires provider credentials and institution linking.",
        env_names=("PLAID_CLIENT_ID", "PLAID_SECRET"),
    ),
    ConnectorCandidate(
        name="Tax filing provider/state portals",
        domain="tax_filing",
        source_url="https://www.avalara.com/",
        credential_class="write_rail",
        maturity="vendor",
        market_state="requires_allowlist",
        auth_modes=("local_oauth", "restricted_api_key"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Reminder/checklist only unless customer authorizes filing integration.",
    ),
    ConnectorCandidate(
        name="Zelle business rail",
        domain="payment_rails",
        source_url="https://www.zelle.com/support/im-small-business-using-zelle",
        credential_class="write_rail",
        maturity="vendor",
        market_state="bank_dependent",
        auth_modes=("manual_export",),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Business availability and limits are bank-dependent; treat as manual/export unless bank API exists.",
    ),
    ConnectorCandidate(
        name="Cash App Pay API",
        domain="payment_rails",
        source_url="https://developers.cash.app/",
        credential_class="write_rail",
        maturity="vendor",
        market_state="stable",
        auth_modes=("restricted_api_key", "oauth"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Cash App Pay integration path; not a generic Cash App account API.",
    ),
    ConnectorCandidate(
        name="Venmo business profile / PayPal Venmo integration",
        domain="payment_rails",
        source_url="https://developer.paypal.com/braintree/articles/guides/payment-methods/venmo/",
        credential_class="write_rail",
        maturity="vendor",
        market_state="stable",
        auth_modes=("manual_export", "restricted_api_key", "oauth"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Use PayPal/Braintree/Venmo-approved path where available; no autonomous money movement.",
    ),
    ConnectorCandidate(
        name="Razorpay Payments API",
        domain="payment_rails",
        source_url="https://razorpay.com/docs/api/",
        credential_class="write_rail",
        maturity="vendor",
        market_state="stable",
        auth_modes=("restricted_api_key", "oauth"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="India payment gateway API; requires Razorpay credentials and payment approval discipline.",
    ),
    ConnectorCandidate(
        name="Payroll/time-clock/e-verify/background checks",
        domain="workforce_compliance",
        source_url="https://www.uscis.gov/e-verify",
        credential_class="oauth",
        maturity="vendor",
        market_state="requires_allowlist",
        auth_modes=("local_oauth", "manual_export"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Connected/custom category for hiring and employee docs.",
    ),
    ConnectorCandidate(
        name="Supplier portal/EDI integrations",
        domain="supplier_edi",
        source_url="https://en.wikipedia.org/wiki/Electronic_data_interchange",
        credential_class="oauth",
        maturity="unknown",
        market_state="unknown",
        auth_modes=("manual_export", "local_oauth", "restricted_api_key"),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Supplier-specific; use WhatsApp/email/manual first.",
    ),
    ConnectorCandidate(
        name="Google Business Profile/Facebook reviews",
        domain="reviews",
        source_url="https://developers.google.com/my-business",
        credential_class="oauth",
        maturity="official",
        market_state="stable",
        auth_modes=("local_oauth",),
        deployment_status="candidate",
        last_verified="2026-05-14",
        freshness_days=30,
        notes="Review response requires platform API access and owner approval.",
        env_names=("GOOGLE_APPLICATION_CREDENTIALS", "FACEBOOK_APP_ID"),
    ),
)


AGENT_CAPABILITIES: tuple[AgentCapability, ...] = (
    AgentCapability(1, "Shift Agent", "no_key_ready", "WhatsApp sick-call intake with local roster/schedule.", "Sheets/CSV roster import.", "Google Calendar/Sheets.", ("productivity/google-workspace",), ("handle_sick_call", "roster_lookup"), ("Google Workspace",), "workspace oauth only if chosen", "coverage/outbound candidate messages", "Do not require Google for local roster mode."),
    AgentCapability(2, "Catering Lead Agent", "no_key_ready", "WhatsApp inquiry/menu/proposal flow with local menu and owner approval.", "Owner uploads menus/photos/docs.", "Payment/POS/CRM connectors.", ("productivity/ocr-and-documents",), ("catering_dispatcher", "parse_catering_inquiry"), ("Stripe MCP", "Square MCP Server", "Zelle business rail", "Cash App Pay API", "Venmo business profile / PayPal Venmo integration", "Razorpay Payments API"), "payments/POS only in connected mode", "final quote/pricing/payment", "No pre-approval prices or booking confirmation."),
    AgentCapability(3, "Multi-Location Coordinator", "no_key_ready", "Local location config plus maps fallback.", "CSV location data.", "POS/inventory across stores.", ("productivity/maps",), ("multi_location_query",), ("Google Maps Grounding Lite MCP", "Square MCP Server", "Clover API/MCP candidate"), "connected POS only for live stock", "transfers", "Do not promise live inventory without POS."),
    AgentCapability(4, "Daily Brief Agent", "no_key_ready", "Read local logs/state and send WhatsApp brief.", "Manual CSV summaries.", "Gmail/Calendar/Sheets connected brief.", ("productivity/google-workspace",), ("send_daily_brief",), ("Google Workspace",), "workspace oauth optional", "none for read-only brief", "Do not invent metrics absent logs."),
    AgentCapability(5, "EOD Reconciliation", "manual_export", "Local event summary and manual register input.", "Register/POS CSV upload.", "Clover/Square/Toast POS.", (), ("eod_reconcile",), ("Square MCP Server", "Clover API/MCP candidate", "Toast POS API", "Manual QBO/POS CSV export"), "POS credentials for automation", "discrepancy resolution", "Do not claim automated POS reconciliation before POS onboarding."),
    AgentCapability(6, "Inventory Tracker", "manual_export", "WhatsApp counts and OCR supplier sheets.", "Supplier XLS/PDF/photo upload.", "POS decrement and supplier reorder.", ("productivity/ocr-and-documents", "productivity/airtable", "productivity/notion"), ("inventory_dispatcher",), ("Airtable MCP", "Notion MCP", "Square MCP Server", "Supplier portal/EDI integrations"), "POS/supplier credentials for writeback", "auto reorder", "No silent supplier orders."),
    AgentCapability(7, "Supplier Coordination", "manual_export", "Local supplier roster and draft WhatsApp/email orders.", "Supplier sheets/doc uploads.", "Supplier portals, EDI, Gmail.", ("productivity/google-workspace", "productivity/ocr-and-documents"), ("supplier_dispatcher",), ("Google Workspace", "Supplier portal/EDI integrations"), "supplier/email oauth", "order send", "No autonomous supplier commitments."),
    AgentCapability(8, "Receiving & QA", "manual_export", "Photo/PDF receiving checks.", "Manual PO/invoice upload.", "Inventory/POS writeback.", ("productivity/ocr-and-documents",), (), ("Supplier portal/EDI integrations", "Square MCP Server"), "POS/inventory credentials", "inventory adjustments", "No writeback without human confirmation."),
    AgentCapability(9, "VIP Customer Agent", "manual_export", "Local catering/customer history and WhatsApp notes.", "CSV/customer list import.", "POS/loyalty history.", (), ("vip_dispatcher",), ("Square MCP Server", "Google Business Profile/Facebook reviews"), "POS/CRM oauth", "customer outreach", "No creepy personal inference."),
    AgentCapability(10, "Catering Follow-up", "no_key_ready", "Catering state plus WhatsApp follow-ups.", "Manual event notes.", "Gmail/CRM.", ("productivity/google-workspace",), ("catering_followup_dispatcher",), ("Google Workspace",), "workspace oauth optional", "customer follow-up sends", "No payment chase without approval."),
    AgentCapability(11, "Festival & Peak Prep", "manual_export", "Local festival calendar and past logs.", "Manual event calendar import.", "Calendar/POS demand data.", ("productivity/google-workspace",), (), ("Google Workspace", "Manual QBO/POS CSV export"), "calendar/POS optional", "inventory/staffing actions", "No mass marketing by default."),
    AgentCapability(12, "Hiring & Onboarding", "manual_export", "WhatsApp intake and local checklist.", "Manual documents upload.", "Drive/e-sign/background checks.", ("productivity/google-workspace", "productivity/ocr-and-documents"), ("hiring_dispatcher",), ("Google Workspace", "DocuSign MCP Connector", "Payroll/time-clock/e-verify/background checks"), "workspace/esign/workforce oauth", "offers/legal docs", "No automated legal signing."),
    AgentCapability(13, "Compliance Calendar", "no_key_ready", "Local compliance JSON and timers.", "Manual license/deadline import.", "Calendar/agency portals.", ("productivity/google-workspace",), ("compliance_owner_query",), ("Google Workspace", "Tax filing provider/state portals"), "agency/calendar optional", "filings", "No tax/legal filing without authorization."),
    AgentCapability(14, "Employee Document Tracker", "manual_export", "Local docs and OCR reminders.", "Manual folder uploads.", "Drive/e-verify/payroll.", ("productivity/google-workspace", "productivity/ocr-and-documents"), ("employee_docs_dispatcher",), ("Google Workspace", "Payroll/time-clock/e-verify/background checks"), "workforce oauth", "legal submissions", "No storing documents in unprotected shared paths."),
    AgentCapability(15, "Cash & AR Agent", "connected_required", "Manual ledger reminders only.", "Invoice/payment CSV upload.", "Stripe/Square/PayPal/QBO/bank.", (), ("cash_ar_dispatcher",), ("Stripe MCP", "Square MCP Server", "PayPal MCP Server", "Zelle business rail", "Cash App Pay API", "Venmo business profile / PayPal Venmo integration", "Razorpay Payments API", "Bank feed/Open Banking APIs", "Intuit QuickBooks Online MCP"), "payment/accounting rails", "all reminders and money actions", "No automated collections or payment movement."),
    AgentCapability(16, "Sales Tax Filing", "connected_required", "Reminder/checklist only.", "POS/tax report uploads.", "State/tax provider filing.", (), ("sales_tax_dispatcher",), ("Tax filing provider/state portals", "Manual QBO/POS CSV export"), "tax portal/provider credentials", "all filings", "No autonomous tax filing."),
    AgentCapability(17, "Unit Economics", "retired_or_folded", "Retired; use #22.", "N/A.", "N/A.", (), (), ("Manual QBO/POS CSV export",), "N/A", "N/A", "Do not rebuild unless revived."),
    AgentCapability(18, "Customer Complaint", "retired_or_folded", "Folded into #9 and #4.", "Manual review paste.", "Review APIs.", (), (), ("Google Business Profile/Facebook reviews", "Yelp MCP"), "review oauth optional", "public replies", "No standalone agent build."),
    AgentCapability(19, "Equipment Maintenance", "manual_export", "Local equipment list and WhatsApp issue intake.", "Manual vendor docs.", "Vendor/IoT APIs.", ("productivity/ocr-and-documents",), ("equipment_maintenance_dispatcher",), ("Supplier portal/EDI integrations",), "vendor credentials optional", "repair commitments", "No auto vendor dispatch."),
    AgentCapability(20, "Owner Wellbeing", "retired_or_folded", "Folded into daily brief/quiet hours.", "N/A.", "Calendar optional.", (), (), ("Google Workspace",), "calendar optional", "none", "Avoid therapy/medical framing."),
    AgentCapability(21, "Expense Bookkeeper", "manual_export", "Receipt/photo extraction and owner-approved draft.", "Manual QBO export/import.", "Intuit QBO MCP.", ("productivity/ocr-and-documents",), ("expense_bookkeeper_dispatcher",), ("Intuit QuickBooks Online MCP", "Manual QBO/POS CSV export"), "QBO oauth for writeback", "all QBO pushes", "Do not custom raw QBO before MCP review."),
    AgentCapability(22, "P&L Anomaly Detective", "manual_export", "Manual CSV/P&L anomaly checks.", "QBO/POS exports.", "QBO/POS connected read.", ("productivity/airtable",), ("pnl_anomaly_dispatcher",), ("Intuit QuickBooks Online MCP", "Square MCP Server", "Clover API/MCP candidate", "Manual QBO/POS CSV export"), "accounting/POS oauth", "no auto action", "No live P&L without data source."),
    AgentCapability(23, "Order Status & Pickup", "connected_required", "Manual board only.", "Manual order status upload.", "POS/KDS order state.", (), (), ("Square MCP Server", "Toast POS API", "Clover API/MCP candidate"), "POS/KDS oauth", "customer status sends", "Do not promise live ETA without POS/KDS."),
    AgentCapability(24, "Upsell & Menu Personalizer", "manual_export", "Local menu and chat context.", "Manual sales history upload.", "POS/cart/loyalty data.", (), (), ("Square MCP Server", "Google Business Profile/Facebook reviews"), "POS/loyalty oauth", "customer upsells", "No dark-pattern upsells."),
    AgentCapability(25, "Third-Party Delivery Coordinator", "connected_required", "Manual escalation/checklist only.", "Manual tablet reports.", "Delivery marketplace APIs/iPaaS.", (), (), ("DoorDash Drive/Marketplace API", "Uber Eats Marketplace API", "Grubhub restaurant integration APIs", "Pipedream MCP"), "marketplace oauth/partner", "order intervention", "No screen-scraping money/order actions by default."),
    AgentCapability(26, "Performance & Training Coach", "manual_export", "Audit-log coaching summaries.", "Manual POS/time-clock exports.", "POS/LMS/time-clock.", (), (), ("Payroll/time-clock/e-verify/background checks", "Square MCP Server"), "workforce/POS oauth", "disciplinary messages", "No punitive automation."),
    AgentCapability(27, "Catering Equipment & Packaging Tracker", "no_key_ready", "Local packaging inventory tied to catering events.", "Manual supplier counts.", "Supplier reorder.", (), (), ("Supplier portal/EDI integrations",), "supplier oauth optional", "orders", "No auto reorder."),
    AgentCapability(28, "Perishable Priority & Waste Reducer", "manual_export", "Manual expiry/photo counts.", "Waste/POS CSV upload.", "Inventory/POS velocity.", ("productivity/ocr-and-documents",), (), ("Square MCP Server", "Clover API/MCP candidate", "Manual QBO/POS CSV export"), "POS/inventory oauth", "discount/disposal decisions", "No auto disposal/markdown."),
    AgentCapability(29, "Slow-Mover Liquidation", "manual_export", "Local inventory and owner-approved suggestions.", "Sales/inventory CSV.", "POS promo writeback.", (), (), ("Square MCP Server", "Clover API/MCP candidate"), "POS oauth", "promotions", "No auto discounts."),
    AgentCapability(30, "Order Accuracy Guardian", "connected_required", "Manual photo checks only.", "Manual order ticket upload.", "KDS/POS order state.", ("productivity/ocr-and-documents",), (), ("Square MCP Server", "Toast POS API"), "POS/KDS oauth", "order fixes", "No live guardian without order source."),
    AgentCapability(31, "Kitchen Load Balancer & ETA", "connected_required", "Manual queue is weak.", "Manual kitchen board.", "KDS/POS timing.", (), (), ("Toast POS API", "Square MCP Server"), "KDS/POS oauth", "customer ETA sends", "No live ETA without telemetry."),
    AgentCapability(32, "Special Request Memory", "no_key_ready", "Local customer notes keyed by identity.", "Manual notes import.", "CRM/POS sync optional.", (), (), ("Airtable MCP", "Notion MCP"), "CRM optional", "staff/customer surfacing", "No sensitive inference."),
    AgentCapability(33, "Loyalty & Punch-Card", "manual_export", "Local phone ledger and WhatsApp reminders.", "Manual purchase uploads.", "POS/loyalty platform.", (), (), ("Square MCP Server", "Clover API/MCP candidate"), "POS oauth", "reward issuance", "No reward fraud-prone auto credits."),
    AgentCapability(34, "Menu Suggestion & Upsell", "manual_export", "Local menu plus current chat.", "Manual item popularity.", "POS/cart history.", (), (), ("Square MCP Server",), "POS/cart oauth", "customer upsells", "No unapproved price claims."),
    AgentCapability(35, "Referral & Review Responder", "manual_export", "Local referral ledger and pasted reviews.", "Manual review exports.", "Google/Facebook/Yelp APIs.", (), (), ("Google Business Profile/Facebook reviews", "Yelp MCP"), "review platform oauth/api", "public replies/rewards", "No public replies without approval."),
    AgentCapability(36, "Credit Customer & Temple Account", "manual_export", "Local ledger and WhatsApp statements.", "QBO/bank exports.", "QBO/bank/payment reconciliation.", (), (), ("Intuit QuickBooks Online MCP", "Bank feed/Open Banking APIs", "Zelle business rail", "Cash App Pay API", "Venmo business profile / PayPal Venmo integration", "Razorpay Payments API"), "accounting/payment oauth", "statements/collections", "No money movement."),
    AgentCapability(37, "New Location Feasibility Scout", "manual_export", "Public web/maps plus local notes.", "Manual demographic docs.", "Paid datasets/maps APIs.", ("productivity/maps",), (), ("Google Maps Grounding Lite MCP", "Yelp MCP"), "paid data/api optional", "investment recommendations", "No final site recommendation as fact."),
    AgentCapability(38, "Local Community Broadcast", "connected_required", "Tiny owner-approved WhatsApp sends only.", "Manual contact list.", "Bulk SMS/email/WhatsApp providers.", (), (), ("Infobip MCP", "Pipedream MCP"), "messaging provider oauth/api", "all broadcasts", "No bulk messaging without compliance review."),
    AgentCapability(39, "Photo Menu Curator", "no_key_ready", "WhatsApp photos plus local menu state.", "Manual menu photos.", "Cloud vision optional.", ("productivity/ocr-and-documents",), (), ("Google Workspace",), "cloud storage optional", "menu publication", "No invented menu details."),
    AgentCapability(40, "Competitor Price Watcher", "manual_export", "Public low-volume web checks.", "Manual competitor price uploads.", "Search/scraping APIs.", ("productivity/maps",), (), ("Google Maps Grounding Lite MCP", "Yelp MCP", "Pipedream MCP"), "search/scraping api", "pricing actions", "No ToS-hostile scraping by default."),
    AgentCapability(41, "Owner Wellbeing & Burnout Guardian", "no_key_ready", "Quiet-hours and owner-load summary.", "Manual calendar notes.", "Calendar optional.", (), (), ("Google Workspace",), "calendar oauth optional", "none", "Avoid therapy/medical framing."),
)


CREDENTIAL_REQUIREMENTS: tuple[CredentialRequirement, ...] = (
    CredentialRequirement("OPENROUTER_API_KEY", "api_key", "Current production model/vision gate."),
    CredentialRequirement("OPENAI_API_KEY", "api_key", "Optional direct-OpenAI Flyer Studio source-edit fallback."),
    CredentialRequirement("KIMI_API_KEY", "api_key", "Optional provider."),
    CredentialRequirement("AIRTABLE_API_KEY", "pat", "Airtable connected mode."),
    CredentialRequirement("NOTION_API_KEY", "api_key", "Notion connected mode."),
    CredentialRequirement("GOOGLE_APPLICATION_CREDENTIALS", "oauth", "Google service account path or credential pointer."),
    CredentialRequirement("STRIPE_SECRET_KEY", "write_rail", "Stripe connected mode."),
    CredentialRequirement("SQUARE_ACCESS_TOKEN", "write_rail", "Square connected mode."),
    CredentialRequirement("QUICKBOOKS_CLIENT_ID", "oauth", "QBO connected mode."),
    CredentialRequirement("QUICKBOOKS_CLIENT_SECRET", "oauth", "QBO connected mode."),
    CredentialRequirement("QUICKBOOKS_REFRESH_TOKEN", "oauth", "QBO connected mode."),
    CredentialRequirement("QUICKBOOKS_REALM_ID", "oauth", "QBO connected mode."),
    CredentialRequirement("QUICKBOOKS_ENVIRONMENT", "oauth", "QBO connected mode."),
    CredentialRequirement("PAYPAL_ACCESS_TOKEN", "write_rail", "PayPal connected mode."),
    CredentialRequirement("PAYPAL_CLIENT_ID", "write_rail", "PayPal connected mode."),
    CredentialRequirement("CLOVER_CLIENT_ID", "oauth", "Clover connected mode."),
    CredentialRequirement("CLOVER_CLIENT_SECRET", "oauth", "Clover connected mode."),
    CredentialRequirement("TOAST_CLIENT_ID", "oauth", "Toast connected mode."),
    CredentialRequirement("TOAST_CLIENT_SECRET", "oauth", "Toast connected mode."),
    CredentialRequirement("DOCUSIGN_CLIENT_ID", "oauth", "DocuSign connected mode."),
    CredentialRequirement("DOCUSIGN_CLIENT_SECRET", "oauth", "DocuSign connected mode."),
    CredentialRequirement("INFOBIP_API_KEY", "api_key", "Infobip connected mode."),
    CredentialRequirement("YELP_API_KEY", "api_key", "Yelp connected mode."),
    CredentialRequirement("GOOGLE_MAPS_API_KEY", "api_key", "Google Maps connected mode."),
    CredentialRequirement("FACEBOOK_APP_ID", "oauth", "Facebook reviews connected mode."),
    CredentialRequirement("PLAID_CLIENT_ID", "oauth", "Bank feed connected mode."),
    CredentialRequirement("PLAID_SECRET", "oauth", "Bank feed connected mode."),
)


_PLACEHOLDER_RE = re.compile(r"(placeholder|fill_me|todo|changeme|dummy|example)", re.IGNORECASE)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _today(value: date | None = None) -> date:
    return value or datetime.utcnow().date()


def _skill_parts(skill_id: str) -> tuple[str, str]:
    if "/" not in skill_id:
        return "", skill_id
    category, name = skill_id.split("/", 1)
    return category, name


def resolve_skill(
    requirement: SkillRequirement,
    *,
    hermes_home: Path,
    hermes_install_root: Path,
    repo_root: Path | None = None,
) -> dict:
    category, name = _skill_parts(requirement.skill_id)
    live = hermes_home / "skills" / category / name / "SKILL.md"
    bundled = hermes_install_root / "skills" / category / name / "SKILL.md"
    if live.exists():
        status = "present"
        root = "live"
    elif bundled.exists():
        status = "present"
        root = "bundled"
    else:
        status = "missing"
        root = "none"

    local_present = False
    if repo_root is not None:
        local_patterns = [
            f"src/agents/**/skills/{name}/SKILL.md",
            f"src/**/skills/{category}/{name}/SKILL.md",
        ]
        # `repo_root.glob(pattern)` returns a generator (always truthy), so
        # `any(glob(p) for p in patterns)` would be True whenever repo_root is set — it never
        # inspected the matches. Wrap each glob in any() so a pattern counts only when it yields
        # a real match.
        local_present = any(any(repo_root.glob(pattern)) for pattern in local_patterns)

    row = asdict(requirement)
    row.update(
        {
            "id": requirement.skill_id,
            "status": status,
            "root": root,
            "local_dev_present": local_present,
        }
    )
    return row


def _read_env(paths: Iterable[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in values:
                values[key] = value
    return values


def _credential_status(value: str | None) -> str:
    if value is None or value == "":
        return "unset"
    if value.startswith("MUTED_"):
        return "muted"
    if _PLACEHOLDER_RE.search(value):
        return "placeholder"
    return "env_present"


def _env_statuses(env_paths: Sequence[Path]) -> dict[str, str]:
    return {name: _credential_status(value) for name, value in _read_env(env_paths).items()}


def inspect_credentials(env_paths: Sequence[Path], env_statuses: dict[str, str] | None = None) -> list[dict]:
    statuses = env_statuses if env_statuses is not None else _env_statuses(env_paths)
    rows: list[dict] = []
    for req in CREDENTIAL_REQUIREMENTS:
        rows.append(
            {
                "name": req.name,
                "class": req.credential_class,
                "status": statuses.get(req.name, "unset"),
                "notes": req.notes,
            }
        )
    return rows


def _connector_configured_status(
    candidate: ConnectorCandidate,
    env_statuses: dict[str, str],
    hermes_home: Path,
) -> str:
    if candidate.env_names:
        statuses = [env_statuses.get(name, "unset") for name in candidate.env_names]
        if all(status == "env_present" for status in statuses):
            return "env_present"
        if any(status == "env_present" for status in statuses):
            return "partial_env"
        if any(status == "placeholder" for status in statuses):
            return "placeholder"
        if any(status == "muted" for status in statuses):
            return "muted"
    for rel in candidate.session_paths:
        if (hermes_home / rel).exists():
            return "oauth_session_present" if "oauth" in candidate.credential_class else "env_present"
    if candidate.deployment_status in {"candidate", "available"}:
        return "candidate_only"
    return "not_probed"


def _parse_plugin_list_fallback(text: str, key: str) -> list[str]:
    values: list[str] = []
    in_plugins = False
    in_target = False
    plugins_indent = 0
    target_indent = 0
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if stripped == "plugins:":
            in_plugins = True
            in_target = False
            plugins_indent = indent
            continue
        if in_plugins and indent <= plugins_indent and not stripped.startswith("-"):
            in_plugins = False
            in_target = False
        if in_plugins and stripped.startswith(f"{key}:"):
            in_target = True
            target_indent = indent
            rest = stripped.split(":", 1)[1].strip()
            if rest.startswith("[") and rest.endswith("]"):
                values.extend(
                    item.strip().strip('"').strip("'")
                    for item in rest.strip("[]").split(",")
                    if item.strip()
                )
            elif rest:
                values.append(rest.strip('"').strip("'"))
            continue
        if in_target:
            if indent <= target_indent and not stripped.startswith("-"):
                in_target = False
                continue
            if stripped.startswith("-"):
                values.append(stripped[1:].strip().strip('"').strip("'"))
    return values


def parse_plugins_state_text(text: str) -> dict[str, list[str]]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        plugins = data.get("plugins") if isinstance(data, dict) else None
        enabled = plugins.get("enabled") if isinstance(plugins, dict) else None
        disabled = plugins.get("disabled") if isinstance(plugins, dict) else None
        return {
            "enabled": [str(item) for item in enabled] if isinstance(enabled, list) else ([enabled] if isinstance(enabled, str) else []),
            "disabled": [str(item) for item in disabled] if isinstance(disabled, list) else ([disabled] if isinstance(disabled, str) else []),
        }
    except Exception:
        pass

    return {
        "enabled": _parse_plugin_list_fallback(text, "enabled"),
        "disabled": _parse_plugin_list_fallback(text, "disabled"),
    }


def parse_plugins_enabled_text(text: str) -> list[str]:
    return parse_plugins_state_text(text)["enabled"]


def _compile_source_only(path: Path) -> None:
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


def _import_cf_router_readonly(plugin_root: Path) -> tuple[bool, str]:
    package_name = "_credential_readiness_cf_router"
    module_names = [package_name, f"{package_name}.actions", f"{package_name}.hooks"]
    previous_modules = {name: sys.modules.get(name) for name in module_names}
    previous_dont_write = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        package = types.ModuleType(package_name)
        package.__path__ = [str(plugin_root)]  # type: ignore[attr-defined]
        sys.modules[package_name] = package
        for short_name in ("actions", "hooks"):
            module_name = f"{package_name}.{short_name}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_root / f"{short_name}.py")
            if spec is None or spec.loader is None:
                return False, f"spec_missing:{short_name}"
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        hooks = sys.modules[f"{package_name}.hooks"]
        if not callable(getattr(hooks, "pre_gateway_dispatch", None)):
            return False, "missing_hook:pre_gateway_dispatch"
        return True, ""
    except Exception as exc:
        return False, f"import_failed:{exc.__class__.__name__}"
    finally:
        sys.dont_write_bytecode = previous_dont_write
        for name, module in previous_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def validate_cf_router(*, hermes_home: Path, config_path: Path, strict: bool = False) -> dict:
    plugin_root = hermes_home / "plugins" / "cf-router"
    result = {
        "name": "cf-router",
        "status": "missing",
        "enabled": False,
        "disabled": False,
        "modules_compile": False,
        "imports_ok": False,
        "detail": "",
    }
    if not plugin_root.exists():
        result["detail"] = "plugin directory missing"
        return result

    compile_targets = (plugin_root / "actions.py", plugin_root / "hooks.py")
    try:
        for target in compile_targets:
            _compile_source_only(target)
        result["modules_compile"] = True
    except Exception as exc:
        result["status"] = "compile_failed"
        result["detail"] = exc.__class__.__name__
        return result

    if strict:
        imports_ok, import_detail = _import_cf_router_readonly(plugin_root)
        result["imports_ok"] = imports_ok
        if not imports_ok:
            result["status"] = "import_failed"
            result["detail"] = import_detail
            return result

    try:
        plugin_state = parse_plugins_state_text(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        result["status"] = "unknown"
        result["detail"] = f"config_unreadable:{exc.__class__.__name__}"
        return result

    enabled = plugin_state["enabled"]
    disabled = plugin_state["disabled"]
    result["enabled"] = "cf-router" in enabled
    result["disabled"] = "cf-router" in disabled
    if result["disabled"]:
        result["status"] = "disabled"
        result["detail"] = "plugins.disabled includes cf-router"
        return result
    if result["enabled"]:
        result["status"] = "present"
    else:
        result["status"] = "disabled"
        result["detail"] = "plugins.enabled does not include cf-router"
    return result


def connector_freshness(candidate: ConnectorCandidate, *, today: date | None = None) -> str:
    current = _today(today)
    verified = parse_date(candidate.last_verified)
    return "fresh" if (current - verified).days <= candidate.freshness_days else "stale"


def _connector_rows(env_statuses: dict[str, str], hermes_home: Path, today: date) -> list[dict]:
    rows: list[dict] = []
    for candidate in CONNECTOR_CANDIDATES:
        row = asdict(candidate)
        row["configured_status"] = _connector_configured_status(candidate, env_statuses, hermes_home)
        row["freshness"] = connector_freshness(candidate, today=today)
        rows.append(row)
    return rows


def _agent_rows() -> list[dict]:
    return [asdict(agent) for agent in AGENT_CAPABILITIES]


def _probe_bridge(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            if response.status != 200:
                return {"status": "disconnected", "detail": f"http_{response.status}"}
            body = json.loads(response.read().decode("utf-8"))
            if body.get("status") == "connected":
                return {"status": "connected"}
            return {"status": "disconnected", "detail": "status_not_connected"}
    except Exception as exc:
        return {"status": "disconnected", "detail": exc.__class__.__name__}


def build_report(options: ReadinessOptions) -> dict:
    today = _today(options.today)
    foundation = [
        resolve_skill(
            req,
            hermes_home=options.hermes_home,
            hermes_install_root=options.hermes_install_root,
            repo_root=options.repo_root,
        )
        for req in FOUNDATION_SKILLS
    ]
    strict_ok = all(row["status"] == "present" for row in foundation)
    env_statuses = _env_statuses(options.env_paths)
    credentials = inspect_credentials(options.env_paths, env_statuses=env_statuses)
    connectors = _connector_rows(env_statuses, options.hermes_home, today)
    plugin = validate_cf_router(
        hermes_home=options.hermes_home,
        config_path=options.config_path,
        strict=bool(options.validate_plugins),
    )
    whatsapp = _probe_bridge(options.bridge_url) if options.check_bridge else {"status": "not_checked"}

    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "strict_foundation_ok": strict_ok,
        "foundation": foundation,
        "plugin": plugin,
        "credentials": credentials,
        "connectors": connectors,
        "agents": _agent_rows(),
        "whatsapp": whatsapp,
        "stale_connectors": [row for row in connectors if row["freshness"] == "stale"],
    }


def format_json_report(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def format_text_report(report: dict) -> str:
    lines = [
        "Credential-Minimized Hermes Readiness",
        "=" * 44,
        f"Strict foundation: {'OK' if report.get('strict_foundation_ok') else 'FAIL'}",
        "",
        "Foundation skills:",
    ]
    for row in report.get("foundation", []):
        lines.append(f"  {row['id']}: {row['status']} ({row['root']})")

    # BL-HERMES-12 hardening #1 (non-blocking WARN): a foundation skill resolving to root=="bundled"
    # exists in the bundled Hermes lib but NOT live (/root/.hermes/skills). The gateway loads from
    # HERMES_HOME (live only), so a bundled-only skill won't load until a skills sync / curator seeds
    # it. The gate still PASSES (install-state present; app rollback can't repair missing bundled
    # skills) — but this surfaces a load-state gap no other gate catches (D1 is project-only; the
    # foundation watchdog ships disabled + excludes namespaced skills). NOT fail-closed here: that
    # would invert the pre-install install-state gate and false-fail legitimate bundled-will-sync
    # states (e.g. a Hermes reinstall). The real between-deploy fix is a post-restart load smoke.
    for row in report.get("foundation", []):
        if row.get("root") == "bundled":
            lines.append(
                f"  WARN: {row['id']} present in bundled Hermes lib but NOT live "
                f"(/root/.hermes/skills) — the live-only loader won't load it until a skills sync "
                f"seeds it (foundation gate still passes)."
            )

    plugin = report.get("plugin") or {}
    if plugin:
        lines.extend(
            [
                "",
                f"Plugin cf-router: {plugin.get('status', 'unknown')} "
                f"(enabled={plugin.get('enabled', False)}, compile={plugin.get('modules_compile', False)})",
            ]
        )

    whatsapp = report.get("whatsapp") or {"status": "unknown"}
    lines.extend(["", f"WhatsApp bridge: {whatsapp.get('status', 'unknown')}"])

    lines.append("")
    lines.append("Credentials:")
    for row in report.get("credentials", []):
        lines.append(f"  {row['name']}: {row['status']} ({row['class']})")

    stale = report.get("stale_connectors") or []
    lines.append("")
    lines.append(f"Connector candidates: {len(report.get('connectors', []))} total, {len(stale)} stale")
    for row in stale[:20]:
        lines.append(f"  STALE {row['name']} last_verified={row['last_verified']}")

    mode_counts: dict[str, int] = {}
    for agent in report.get("agents", []):
        mode_counts[agent["default_mode"]] = mode_counts.get(agent["default_mode"], 0) + 1
    if mode_counts:
        lines.append("")
        lines.append("Agent modes:")
        for mode in sorted(mode_counts):
            lines.append(f"  {mode}: {mode_counts[mode]}")

    return "\n".join(lines)


def _path_tuple(values: Sequence[str] | None, default: tuple[Path, ...]) -> tuple[Path, ...]:
    if values is None:
        return default
    return tuple(Path(value) for value in values)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report credential-minimized Hermes readiness.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--strict-foundation", action="store_true")
    parser.add_argument("--hermes-home", type=Path, default=DEFAULT_HERMES_HOME)
    parser.add_argument("--hermes-install-root", type=Path, default=DEFAULT_HERMES_INSTALL_ROOT)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--env", dest="env_paths", action="append")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--check-bridge", action="store_true")
    parser.add_argument("--bridge-url", default=DEFAULT_BRIDGE_URL)
    parser.add_argument("--validate-plugin", action="append", default=[])
    parser.add_argument("--today", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    unsupported_plugins = [p for p in args.validate_plugin if p != "cf-router"]
    if unsupported_plugins:
        sys.stderr.write(f"unsupported plugin validation: {', '.join(unsupported_plugins)}\n")
        return 2

    options = ReadinessOptions(
        hermes_home=args.hermes_home,
        hermes_install_root=args.hermes_install_root,
        repo_root=args.repo_root,
        env_paths=_path_tuple(args.env_paths, DEFAULT_ENV_PATHS),
        config_path=args.config,
        strict_foundation=args.strict_foundation,
        check_bridge=args.check_bridge,
        bridge_url=args.bridge_url,
        validate_plugins=tuple(args.validate_plugin),
        today=parse_date(args.today) if args.today else None,
    )
    report = build_report(options)

    if args.format == "json":
        print(format_json_report(report))
    else:
        print(format_text_report(report))

    if args.validate_plugin:
        status = (report.get("plugin") or {}).get("status")
        if status == "unknown":
            return 2
        if status != "present":
            return 1

    if args.strict_foundation and not report["strict_foundation_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
