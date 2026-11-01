"""Who may call what. One ordered table, first match wins, anything unmatched under /api is denied (ADR 0008).

The middleware in main.py is the only place this is enforced; routes never check roles themselves
(except data scoping, e.g. managers only seeing their own reports).
"""
import re

ADMIN, HR, APPROVER, MANAGER, AUDITOR = "admin", "hr_ops", "approver", "manager", "auditor"
ROLES = (ADMIN, HR, APPROVER, MANAGER, AUDITOR)
STAFF = {ADMIN, HR, APPROVER, AUDITOR}
PUBLIC = "public"      # no session needed
SIGNED_IN = "signed_in"  # any active user

R, W = {"GET", "HEAD"}, {"POST", "PUT", "PATCH", "DELETE"}
ANY = R | W

# (methods, path regex, allowed roles | PUBLIC | SIGNED_IN)
RULES: list[tuple[set, re.Pattern, object]] = [(m, re.compile(p), roles) for m, p, roles in [
    (ANY, r"^/api/auth/login$", PUBLIC),
    (ANY, r"^/api/auth/(me|logout|password)$", SIGNED_IN),
    (ANY, r"^/api/portal/", PUBLIC),

    (ANY, r"^/api/users(/|$)", {ADMIN}),
    (R, r"^/api/work-queue$", STAFF),
    (W, r"^/api/work-queue/\d+/resolve$", {ADMIN, HR, APPROVER}),

    # Approval decisions are a policy gate: approvers only, never the operators who prepared them.
    (W, r"^/api/integrations/[^/]+/candidates/[^/]+/decision$", {APPROVER}),
    (W, r"^/api/integrations/[^/]+/offer/decision$", {APPROVER}),
    (R, r"^/api/approvals$", STAFF),

    # Adding joiners from a connector is onboarding work.
    (W, r"^/api/connectors/[^/]+/(import|records/[^/]+/onboard)$", {HR, ADMIN}),
    (R, r"^/api/(connectors|integrations)(/|$)", STAFF),
    (W, r"^/api/(connectors|integrations)(/|$)", {ADMIN}),

    (R, r"^/api/logs/", {ADMIN, HR, APPROVER, AUDITOR}),
    (R, r"^/api/quality$", STAFF),
    (W, r"^/api/quality/eval$", {ADMIN}),

    # A portal link lets the holder act as the joiner, so only the people who send it can fetch it.
    (R, r"^/api/employees/[^/]+/portal-link$", {HR}),
    # Core onboarding. Managers read, scoped to their own reports by main.get_emp / list_employees.
    (R, r"^/api/(employees|stats$|events$|documents/[^/]+/file$)", STAFF | {MANAGER}),
    (R, r"^/api/(templates|rules|policies)(/|$)", STAFF),
    (W, r"^/api/hr/chat$", STAFF),
    (W, r"^/api/(employees|items|escalate$|gmail/poll$|ingest$|onboard$|templates|rules)(/|$)", {HR}),
]]


def allowed(method: str, path: str, role: str | None) -> bool | None:
    """True/False for a decision; None when the path is outside /api (not ours to judge)."""
    if not path.startswith("/api/"):
        return None
    for methods, rx, roles in RULES:
        if method in methods and rx.search(path):
            if roles == PUBLIC:
                return True
            if role is None:
                return False
            return roles == SIGNED_IN or role in roles
    return False


def is_public(method: str, path: str) -> bool:
    for methods, rx, roles in RULES:
        if method in methods and rx.search(path):
            return roles == PUBLIC
    return False
