techstack - next.js, tailwind, fastapi, python, flakordb, docs verify - https://awaithumans.dev/awaitverify/ , gmail


platform -> employee document collection email template (customisable by hr) -> hr fills the name roles and selects the template and hits send to the person -> person receves the email reads/goes through it and send the email with the required attachments (docs) -> platform will read the email of the employee/person extract the docs/attachments -> send notification to the hr that received the docs and running verification -> the platform will create the flakordb entity relation graph and evaluate it if there are missing info or some dat is not matching it will flag in the dashboard -> the complete info will be shown in the dashboard. 

email template -> customasible
required documents template as per role -> customisable

email -> google email i will provide the gmail credentials like secretid and anon key or other related info

template generate some samples fo r now yourself down the line i will provide u the detailed template 

# Build an AI-Powered HR Onboarding & Integration Platform

Act as a senior product designer, enterprise SaaS architect, staff-level full-stack engineer and AI agent engineer.

Your task is to design and build a premium, modern, enterprise-grade *AI-Powered HR Onboarding and Integration Platform*.

This is a hackathon project. The application must demonstrate real working functionality, not just static screens or a UI prototype.

## 1. Product vision

We are building an AI-powered employee onboarding integration platform inspired by the integration configuration and attribute-mapping experience of Aquera.

The platform allows customers to:

- Connect candidate data sources and HR systems.
- Import candidate data.
- Create multiple integrations.
- Configure source and target systems.
- Use an AI agent to configure integrations through conversation.
- Automatically discover source and target attributes.
- Suggest attribute mappings using AI.
- Edit and approve mappings.
- Configure required onboarding documents.
- Generate document request templates.
- Require HR approval before sending document requests.
- Collect and validate candidate documents.
- Require HR verification of submitted documents.
- Generate and send offer letters after successful HR verification.
- Schedule integrations for automatic execution.

The platform must support multiple customers, with strict separation of their data and configurations.

## 2. Important product rules

These rules are mandatory.

1. Customers can create multiple independent integrations.
2. Each integration has a configurable source and target.
3. Initially, we will implement our own Candidate Portal and HR Portal as demonstration connectors.
4. Candidate data can also be imported manually.
5. The AI agent assists with integration setup and attribute mapping.
6. AI-suggested mappings must be editable and require approval before activation.
7. HR can configure document requirements independently of integration mappings.
8. Document requirements must be available as context to the AI agent for the relevant customer, role, department, grade and location.
9. Document request templates must be reviewed and approved by HR before they are sent.
10. Candidate documents can be checked automatically, but final document verification requires HR approval.
11. Offer letters can only be sent after HR has approved the submitted documents.
12. Offer letter templates and required content are configurable by HR.
13. Integration execution can be scheduled.
14. Once an integration is configured, approved and activated, eligible processing should happen automatically.
15. Do not implement the email ingestion agent. Another team member is responsible for it. Provide clear integration boundaries for the email sending service.
16. Do not hardcode document requirements, company policies, offer-letter content or customer-specific business rules.

## 3. Technology stack

Use the following confirmed technologies:

- Frontend: React.js
- Language: TypeScript
- Database: PostgreSQL

Do not assume an ORM, backend framework, AI provider, UI library or cloud provider without checking the existing project or asking for confirmation.

First inspect the existing repository and understand its structure.

If the repository is empty, propose a clean project structure and explain your recommended backend and tooling choices before implementing them.

Do not replace existing working code unnecessarily.

## 4. Product UI/UX vision — extremely important

The UI must feel like a premium enterprise SaaS product.

Visual inspiration:
- Linear
- Stripe Dashboard
- Ramp
- Vercel
- Modern enterprise integration platforms
- Premium HRIS applications

Do not copy their branding or interfaces.

Design characteristics:

- Premium, minimal, sophisticated interface.
- Clear information hierarchy.
- Carefully designed typography.
- Consistent spacing and alignment.
- Beautiful cards and data tables.
- Subtle borders and shadows.
- Professional color palette.
- Excellent empty states.
- Meaningful loading and error states.
- Responsive layouts.
- Smooth, restrained transitions.
- Accessible interactions.

Avoid:
- Generic AI-generated dashboard appearance.
- Excessive gradients.
- Excessive rounded cards.
- Random colors.
- Oversized headings.
- Emoji-based navigation.
- Unnecessary animations.
- Cluttered interfaces.
- Fake statistics presented as real data.

Use a coherent design system with reusable tokens for typography, spacing, colors, borders, shadows and component states.

The UI should look polished enough to present to enterprise customers and hackathon judges.

### Main application layout

Build a professional application shell with:

- Collapsible left sidebar.
- Customer/workspace switcher.
- Global search.
- Notifications.
- User profile.
- Main content area.
- Breadcrumb navigation.
- Consistent page headers and actions.

### Main navigation

1. Dashboard
2. Connectors
3. Integrations
4. Candidates
5. Attribute Mapping
6. Document Requirements
7. Document Templates
8. Offer Letter Templates
9. Onboarding & Verification
10. Approval Center
11. Execution History
12. Settings

Make navigation functional. Do not create dead links.

## 5. Customer and authentication foundation

Implement a multi-customer architecture.

Each customer should have:

- Customer profile.
- HR users.
- Connector instances.
- Multiple integrations.
- Candidate records.
- Employee records.
- Document requirements.
- Offer templates.
- Approval history.
- Integration execution history.

Implement role-based permissions for HR and other relevant users.

All customer-owned data must be isolated using customer identifiers and server-side authorization.

## 6. Connectors module

Build a dedicated Connectors section.

For the hackathon, implement two custom connectors:

### A. Candidate Portal

It should contain realistic candidate records and allow the integration to retrieve:

- Candidate ID.
- First name.
- Last name.
- Email.
- Phone.
- Date of birth, where available.
- Education.
- Employment history.
- Department.
- Designation.
- Location.
- Joining date.
- Candidate status.

### B. HR Portal

It should simulate the target HR system and contain employee records with configurable attributes.

Allow users to inspect available fields and sample records.

### C. Candidate import

Support importing candidate records through CSV and Excel files.

Include:
- File selection.
- Column discovery.
- Preview.
- Validation.
- Import summary.
- Duplicate handling.

Connectors should expose a common interface so additional external systems can be added later.

## 7. Integrations module

Customers must be able to create multiple integrations.

Each integration should have:

- Name.
- Description.
- Source connector.
- Target connector.
- Configuration.
- Attribute mappings.
- Execution mode.
- Schedule.
- Status.
- Version.
- Execution history.

Example integrations:

- Engineering Hiring: Candidate Portal → HR Portal.
- Finance Hiring: Candidate Import → HR Portal.
- Sales Hiring: Candidate Portal → HR Portal.

### Integration creation wizard

Create a polished multi-step integration setup experience.

Step 1: Enter integration name and description.

Step 2: Select source connector.

Step 3: Select target connector.

Step 4: Let the AI agent ask relevant configuration questions.

Step 5: Discover source and target schemas.

Step 6: Generate AI-assisted attribute mappings.

Step 7: Review and edit mappings.

Step 8: Validate the mapping.

Step 9: Configure execution schedule.

Step 10: Review and activate the integration.

Show progress and allow users to navigate backward without losing saved configuration.

## 8. AI integration assistant

Create a conversational AI interface integrated into the integration configuration experience.

The agent should:

- Ask relevant questions.
- Understand integration requirements.
- Read source and target schemas.
- Retrieve sample records.
- Suggest source-to-target mappings.
- Explain mapping decisions.
- Identify missing required fields.
- Suggest transformations.
- Help configure execution rules.
- Validate integration configuration.

Example:

HR: I want to integrate our engineering candidates into the HR system.

Agent: Which candidate records should be transferred?

HR: Only candidates whose status is Offer Accepted.

Agent: Should I transfer all accepted candidates or only Engineering candidates?

HR: Only Engineering.

Agent: What should happen if an employee with the same email already exists?

HR: Update the existing employee.

The AI must use restricted backend tools. Never allow the LLM to directly execute arbitrary SQL or bypass server-side validation.

## 9. Attribute mapping experience

This is one of the most important features.

Build a sophisticated mapping interface.

Display source and target attributes side by side.

For each mapping show:

- Source field.
- Target field.
- AI confidence or match rationale.
- Transformation.
- Required status.
- Mapping status.

Allow HR to:

- Change source fields.
- Change target fields.
- Add mappings.
- Remove mappings.
- Configure default values.
- Add transformations.
- Mark required fields.
- Preview mapped records.
- Validate mappings.
- Save mapping versions.
- Approve mappings.

Support examples such as:

candidate.first_name → employee.first_name

candidate.email → employee.work_email

candidate.joining_date → employee.start_date

candidate.department_name → employee.department_id

candidate.full_name → employee.first_name + employee.last_name

Do not simply render static mapping examples. Connect the UI to actual persisted mapping configurations.

## 10. Document Requirements module

This must be a separate HR configuration module.

HR should configure required documents based on:

- Department.
- Role.
- Grade.
- Location.

Each requirement should support:

- Document name.
- Description.
- Mandatory or optional.
- Accepted file types.
- Expiry requirements, where relevant.
- Validation instructions.
- Active/inactive status.

The AI assistant must retrieve the applicable requirements dynamically from the database for the selected candidate.

Never hardcode document lists into AI prompts.

## 11. Document Template module

Generate a document request template using:

- Customer details.
- Candidate details.
- Role and department.
- Applicable required documents.
- Configured template content.

Support a structured template payload:

{
  "content": "",
  "required_doc": [],
  "to": ""
}

HR must be able to preview, edit, approve and reject the template.

Only approved template versions can be handed over to the external email sending service.

Do not implement inbound email processing or the email agent.

## 12. Onboarding and document verification

The system should track:

- Candidate information.
- Requested documents.
- Received documents.
- Document classification.
- Extracted information.
- Preliminary validation results.
- Rejection reasons.
- Resubmission history.
- HR review status.

AI may check:
- Document type.
- Readability.
- Required fields.
- Expiry dates.
- Inconsistent information.

HR must make the final verification decision.

Maintain an audit trail for document decisions.

## 13. Offer Letter Templates

Create a dedicated Offer Letter Templates module.

HR should configure:
- Template content.
- Company information.
- Designation.
- Department.
- Joining date.
- Compensation details.
- Reporting manager.
- Other company-defined fields.

Support dynamic placeholders such as:

{{candidate.first_name}}
{{candidate.designation}}
{{candidate.joining_date}}
{{candidate.location}}
{{company.name}}

The application can prepare an offer letter automatically after document verification is approved.

Sending must remain blocked until HR verification is complete.

Use approved template versions and preserve generated offer-letter records.

## 14. Automation and scheduling

Each integration should support:

- Manual execution.
- Scheduled execution.
- Event-driven execution where supported.

Show:
- Next execution.
- Last execution.
- Execution status.
- Records processed.
- Successful records.
- Failed records.
- Error details.

Persist every execution.

Implement retries safely and prevent duplicate employee creation.

## 15. Approval Center

Create a dedicated approval interface.

Show separate approval categories:

1. Document request template approval.
2. Candidate document verification.
3. Offer-related approval if required by customer configuration.

Each approval must show:
- Candidate or integration.
- Requested action.
- Submitted information.
- Status.
- Approver.
- Timestamp.
- Approve/reject action.
- Rejection reason.

The two mandatory approval gates are document request template approval and candidate document verification.

## 16. Database design

Design a normalized PostgreSQL schema.

Consider entities such as:

- customers
- users
- connectors
- connector_instances
- integrations
- integration_versions
- integration_mappings
- mapping_rules
- candidates
- employees
- departments
- document_requirement_sets
- document_requirements
- document_templates
- offer_letter_templates
- onboarding_instances
- documents
- document_reviews
- approvals
- integration_schedules
- integration_executions
- audit_logs

Use appropriate:
- Foreign keys.
- Unique constraints.
- Indexes.
- Timestamps.
- Status enums.
- Customer-level isolation.

Do not create unnecessary tables without explaining their purpose.

## 17. Engineering quality

Follow these standards:

- Strict TypeScript.
- Reusable components.
- Clean architecture.
- Separation of UI and business logic.
- Server-side validation.
- Consistent error handling.
- Secure file upload handling.
- RBAC.
- Audit logging.
- Database transactions where appropriate.
- Idempotent automation.
- No hardcoded secrets.
- No arbitrary SQL execution by the AI.
- No fake API integrations presented as real integrations.

Use realistic seeded demo data where necessary, clearly distinguishable from production data.

## 18. Implementation approach

Do not attempt to build the entire application in one giant code generation step.

First:

1. Inspect the repository.
2. Understand existing code and dependencies.
3. Present the proposed architecture.
4. Establish the design system.
5. Establish the application layout.
6. Build the foundation.
7. Implement features incrementally.
8. Test each workflow.
9. Verify the actual application in a browser.

Prioritize working functionality and UI quality equally.

For each feature:
- Define expected behaviour.
- Build the frontend.
- Implement the backend.
- Persist data.
- Test the full flow.
- Verify error and empty states.

Do not claim functionality works unless it has been tested.

## 19. First task: Phase 1 only

For now, implement only the foundation:

- Application shell and navigation.
- Customer/workspace foundation.
- Authentication and HR roles.
- Custom Candidate Portal.
- Custom HR Portal.
- Candidate listing and details.
- Candidate import interface.
- PostgreSQL persistence.
- Basic dashboard.

Do not implement the email agent.

Do not jump into document verification, offer letters, AI mapping or scheduling yet.

### Phase 1 completion criteria

A customer should be able to:

1. Log in.
2. Access the workspace.
3. Open the Candidate Portal connector.
4. View candidate records.
5. Import candidate data.
6. Open the HR Portal connector.
7. View HR employee records.
8. Navigate to the integrations section.
9. See a professional, polished application experience.

Only after Phase 1 works should we proceed to integration creation and AI-assisted attribute mapping.

## Final design instruction

Think like a senior product designer building a SaaS product that will be evaluated by experienced enterprise software judges.

Every screen should feel intentional, every action should have a clear purpose, and every workflow should be easy to understand.

*Do not sacrifice design quality for implementation speed. Do not sacrifice working functionality for visual appearance.*

Build something that looks like a real enterprise product and behaves like one.


OPENAI_API_KEY=<set in .env — never commit keys>