"""Seed the connector catalog, this workspace's instances and demo records (only when absent)."""
import time

from graph import q, q1, new_id
from .candidates import CandidatePortal
from .hr_portal import HrPortal

CATALOG = [
    {"key": "candidate_portal", "name": "Candidate Portal", "role": "source", "record_kind": "candidate", "demo": True,
     "description": "Built-in demonstration applicant portal"},
    {"key": "candidate_import", "name": "Candidate Import", "role": "source", "record_kind": "candidate", "demo": False,
     "description": "Candidates uploaded from CSV, Excel (.xlsx) or JSON"},
    {"key": "hr_portal", "name": "HR Portal", "role": "target", "record_kind": "employee", "demo": True,
     "description": "Built-in demonstration HR system with configurable fields"},
]

CANDIDATES = [
    ("C-2041", "Ananya", "Krishnan", "ananya.krishnan@example.com", "+91 98450 11223", "1998-02-14", ["B.Tech Computer Science, NIT Trichy"], ["Infosys (2020–2024)"], "Engineering", "Software Engineer", "G3", "Bengaluru", "2026-12-01", "offer_accepted"),
    ("C-2042", "Vikram", "Joshi", "vikram.joshi@example.com", "+91 98200 44556", "1991-07-03", ["MBA, IIM Indore"], ["HDFC Bank (2015–2019)", "Zoho (2019–2026)"], "Sales", "Account Executive", "G3", "Mumbai", "2026-12-08", "hired"),
    ("C-2043", "Sneha", "Reddy", "sneha.reddy@example.com", "+91 99000 77881", "2003-11-19", ["B.E. Electronics, BMS College"], [], "Engineering", "Software Engineering Intern", "G1", "Bengaluru", "2026-12-15", "offer_accepted"),
    ("C-2044", "Rahul", "Mehta", "rahul.mehta@example.com", "+91 97300 12345", "1993-05-22", ["M.Tech, IIT Bombay"], ["TCS (2016–2021)", "Atlassian (2021–2026)"], "Engineering", "Senior Software Engineer", "G5", "Pune", "2026-11-24", "hired"),
    ("C-2045", "Emily", "Carter", "emily.carter@example.com", "+1 212 555 0142", "1989-09-09", ["BS Computer Science, NYU"], ["IBM (2012–2026)"], "Engineering", "Solutions Architect", "G5", "New York", "2026-12-07", "offer_accepted"),
    ("C-2046", "Arjun", "Nair", "arjun.nair@example.com", "+91 98470 33221", "1996-01-30", ["B.Com, Christ University"], ["Swiggy (2019–2026)"], "Marketing", "Growth Marketer", "G3", "Bengaluru", "2026-12-01", "offer_accepted"),
    ("C-2047", "Fatima", "Sheikh", "fatima.sheikh@example.com", "+91 99870 65432", "1997-04-11", ["B.Sc, St. Xavier's"], ["Freshworks (2020–2026)"], "Sales", "", "G2", "Mumbai", "2026-12-10", "offer_accepted"),
    ("C-2048", "Karan", "Malhotra", "karan.malhotra@example.com", "+91 98110 99887", "1995-12-05", ["B.Tech, DTU"], ["Flipkart (2018–2026)"], "Engineering", "QA Engineer", "G2", "Hyderabad", "", "interviewing"),
    ("C-2049", "Lakshmi", "Iyer", "lakshmi.iyer@example.com", "+91 94440 22110", "1992-08-27", ["MA HRM, TISS"], ["Wipro (2014–2026)"], "Human Resources", "HR Business Partner", "G4", "Mumbai", "2026-12-14", "applied"),
    ("C-2050", "Daniel", "Brooks", "daniel.brooks@example.com", "+1 512 555 0199", "1994-03-16", ["BBA, UT Austin"], ["Oracle (2017–2026)"], "Sales", "Sales Development Representative", "G2", "Austin", "2026-12-01", "rejected"),
]

HR_EMPLOYEES = [
    ("EMP-10021", "Priya", "Menon", "priya.menon@azentio.example", "Engineering", "Engineering Manager", "G7", "Bengaluru", "", "2019-04-01"),
    ("EMP-10034", "Vikram", "Rao", "vikram.rao@azentio.example", "Sales", "Sales Director", "G7", "Mumbai", "", "2018-09-17"),
    ("EMP-10077", "Anita", "Desai", "anita.desai@azentio.example", "Human Resources", "Head of People", "G8", "Mumbai", "", "2017-01-09"),
    ("EMP-10102", "Sarah", "Kim", "sarah.kim@azentio.example", "Engineering", "Director of Engineering", "G8", "New York", "", "2020-06-15"),
    ("EMP-10155", "Rahul", "Mehta", "rahul.mehta@example.com", "Engineering", "Software Engineer", "G3", "Pune", "Priya Menon", "2026-11-24"),
]


def seed_apps():
    """App connectors arrived after the first seed, so add any that are missing to existing workspaces too."""
    from .apps import CATALOG as APP_CATALOG
    for c in APP_CATALOG:
        if not q1("MATCH (c:Connector {key:$k}) RETURN c", k=c["key"]):
            q("CREATE (c:Connector) SET c = $p", p=c)
            q("MATCH (c:Connector {key:$k}) CREATE (i:ConnectorInstance {id:$id, key:$k, name:$n, created:$t})-[:OF]->(c)",
              k=c["key"], id=new_id("ci"), n=c["name"], t=time.time())


def seed_connectors():
    seed_apps()
    if q1("MATCH (c:Connector) WHERE c.role <> 'app' RETURN c LIMIT 1"):
        return
    now = time.time()
    ids = {}
    for c in CATALOG:
        q("CREATE (c:Connector) SET c = $p", p=c)
        ids[c["key"]] = new_id("ci")
        q("MATCH (c:Connector {key:$k}) CREATE (i:ConnectorInstance {id:$id, key:$k, name:$n, created:$t})-[:OF]->(c)",
          k=c["key"], id=ids[c["key"]], n=c["name"], t=now)
    keys = [f.key for f in CandidatePortal.FIELDS]
    portal = CandidatePortal()
    for row in CANDIDATES:
        portal.upsert_candidate(ids["candidate_portal"], dict(zip(keys, row)))
    hr = HrPortal()
    hr_keys = ["employee_number", "first_name", "last_name", "work_email", "department_name", "job_title", "grade",
               "location", "manager_name", "start_date"]
    for row in HR_EMPLOYEES:
        hr.upsert_record(ids["hr_portal"], {**dict(zip(hr_keys, row)), "employment_status": "active"})
