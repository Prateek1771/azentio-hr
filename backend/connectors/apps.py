"""App connectors: tools a new joiner gets access to (Slack, Jira, ServiceNow, Google Workspace).

ponytail: these are dummies. Every action is written as a Record in the connector, so HR sees exactly
what would have happened, but no external API is called. Swap `perform` for the vendor SDK call per app
when real credentials exist; the action names and parameters stay the same.
"""
import hashlib

from .base import Connector, FieldSpec, RecordRejected

ACTION_FIELDS = [
    FieldSpec("action", "Action", required=True), FieldSpec("person", "Person"), FieldSpec("email", "Email"),
    FieldSpec("resource", "Channel / project / group"), FieldSpec("mode", "Mode"), FieldSpec("status", "Status"),
    FieldSpec("reference", "Reference"), FieldSpec("message", "Message"), FieldSpec("requested_by", "Requested by"),
]

# action -> (label, needs a channel/project/group name)
ACTIONS = {
    "slack": {"create_account": ("Create Slack account", False), "add_to_channel": ("Add to channel", True),
              "notify_channel": ("Post in channel", True)},
    "jira": {"create_account": ("Create Jira account", False), "add_to_project": ("Add to project", True),
             "notify_admin": ("Notify project admin", True)},
    "servicenow": {"create_ticket": ("Raise ServiceNow request", True)},
    "google_workspace": {"create_account": ("Create mailbox", False), "add_to_group": ("Add to Google group", True)},
}


def _ref(prefix: str, *parts: str) -> str:
    return prefix + hashlib.sha1("|".join(parts).encode()).hexdigest()[:7].upper()


class AppConnector(Connector):
    record_kind = "activity"
    FIELDS = ACTION_FIELDS
    REF = "X-"

    def perform(self, instance_id: str, action: str, person: str, email: str, resource: str = "", mode: str = "direct",
                requested_by: str = "Integration") -> dict:
        """Do one access action for one person. mode 'direct' does it; 'request' asks the channel/project admin."""
        actions = ACTIONS[self.key]
        if action not in actions:
            raise RecordRejected(f"{self.key} can't {action.replace('_', ' ')}; it can {', '.join(actions)}")
        label, needs_resource = actions[action]
        if needs_resource and not resource:
            raise RecordRejected(f"{label} needs a {'channel' if self.key == 'slack' else 'project or group'} name")
        existing = self._existing(instance_id, action, email, resource)
        if existing:  # repeat-safe: the same grant twice is one grant
            return {**existing["values"], "id": existing["id"], "repeat": True}
        values = {"action": action, "person": person, "email": email, "resource": resource, "mode": mode,
                  "requested_by": requested_by, **self._outcome(action, person, email, resource, mode)}
        rid = self._write(instance_id, values)
        return {**values, "id": rid, "repeat": False}

    def _existing(self, instance_id: str, action: str, email: str, resource: str):
        for row in self.list_records(instance_id, page_size=10_000)["items"]:
            v = row["values"]
            if v.get("action") == action and v.get("email", "").lower() == email.lower() and v.get("resource", "") == resource:
                return row
        return None

    def _outcome(self, action, person, email, resource, mode) -> dict:
        raise NotImplementedError


class Slack(AppConnector):
    key = "slack"

    def _outcome(self, action, person, email, resource, mode):
        ch = resource if resource.startswith("#") or not resource else f"#{resource}"
        if action == "create_account":
            return {"status": "invited", "reference": _ref("U", email), "message": f"Invited {email} to the Azentio workspace"}
        if action == "add_to_channel" and mode == "request":
            return {"status": "requested", "reference": _ref("M", email, ch), "resource": ch,
                    "message": f"Asked the {ch} channel admin to add {person}"}
        if action == "add_to_channel":
            return {"status": "added", "reference": _ref("M", email, ch), "resource": ch, "message": f"Added {person} to {ch}"}
        return {"status": "posted", "reference": _ref("M", email, ch, "post"), "resource": ch,
                "message": f"Posted in {ch}: please welcome {person} ({email}) who is joining the team"}


class Jira(AppConnector):
    key = "jira"

    def _outcome(self, action, person, email, resource, mode):
        if action == "create_account":
            return {"status": "created", "reference": _ref("acc-", email), "message": f"Created Jira account for {email}"}
        if action == "add_to_project" and mode != "request":
            return {"status": "added", "reference": _ref("PRJ-", email, resource), "message": f"Added {person} to project {resource} as a member"}
        return {"status": "requested", "reference": _ref("PRJ-", email, resource, "req"),
                "message": f"Notified the {resource} project admin to give {person} ({email}) access"}


class ServiceNow(AppConnector):
    key = "servicenow"

    def _outcome(self, action, person, email, resource, mode):
        num = "RITM" + str(int(hashlib.sha1(f"{email}|{resource}".encode()).hexdigest(), 16) % 10_000_000).zfill(7)
        return {"status": "open", "reference": num, "message": f"Request {num}: {resource} for {person} ({email})"}


class GoogleWorkspace(AppConnector):
    key = "google_workspace"

    def _outcome(self, action, person, email, resource, mode):
        if action == "create_account":
            return {"status": "created", "reference": _ref("G", email), "message": f"Created mailbox {email} for {person}"}
        return {"status": "added", "reference": _ref("G", email, resource), "message": f"Added {email} to group {resource}"}


APPS = {c.key: c for c in (Slack, Jira, ServiceNow, GoogleWorkspace)}
CATALOG = [
    {"key": "slack", "name": "Slack", "role": "app", "record_kind": "activity", "demo": True,
     "description": "Workspace accounts, channel membership and channel announcements (demo, no real API calls)"},
    {"key": "jira", "name": "Jira", "role": "app", "record_kind": "activity", "demo": True,
     "description": "Jira accounts, project access and project-admin requests (demo)"},
    {"key": "servicenow", "name": "ServiceNow", "role": "app", "record_kind": "activity", "demo": True,
     "description": "IT requests for laptops, hardware and access (demo)"},
    {"key": "google_workspace", "name": "Google Workspace", "role": "app", "record_kind": "activity", "demo": True,
     "description": "Company mailboxes (Gmail) and Google groups (demo)"},
]
