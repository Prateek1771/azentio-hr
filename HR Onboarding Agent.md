## **HR Onboarding Agent**

Category: AI for HR Operations & Employee Lifecycle Automation  
Applicable Domains: Human Resources, IT, Administration, Compliance and Enterprise Operations  
Focus: New Employee Onboarding, Document Collection, Task Orchestration, Policy Assistance and Onboarding Readiness

### **Problem Statement**

Employee onboarding involves several departments and activities, including offer acceptance, document collection, identity and background verification, HR record creation, IT provisioning, policy acknowledgement, training and orientation. A smooth onboarding experience helps new employees become productive quickly and creates a positive first impression of the organisation.  
These activities are frequently coordinated through emails, spreadsheets and separate HR, IT and facilities applications. Missing documents, unclear responsibilities and delayed approvals can prevent employees from being fully prepared on their joining date. New joiners may arrive without a laptop, system access or a completed employee record, while HR teams spend considerable time chasing documents and following up with other departments.  
For example, a new software engineer joining in Bengaluru may require identity documents, education certificates, a background verification check, a laptop, an email account, access to development tools and completion of an information security policy acknowledgement. If the laptop request cannot be raised until the employee record is created, and the employee record cannot be created until documents are verified, a single delayed document can affect the entire onboarding plan.  
The challenge is to build an AI-powered HR Onboarding Agent that coordinates the employee onboarding journey, identifies outstanding requirements, executes approved workflows and provides employees and HR teams with clear progress visibility.  
The agent should support new employees through a conversational interface while managing actual onboarding tasks and records in the application. It must answer questions using approved policy information, initiate tasks through backend services and keep every stakeholder informed of what is pending and who is responsible.  
The solution must handle change. Joining dates move, roles are revised and documents are rejected. The application should recalculate affected tasks and deadlines rather than requiring HR teams to rebuild the onboarding plan manually.  
Participants are encouraged to introduce additional onboarding automation capabilities and innovative approaches beyond the specified scenarios. Solutions demonstrating measurable improvements in onboarding readiness and employee experience will receive additional consideration within the evaluation framework.  
The organiser will assess actual application behaviour rather than a static dashboard, predefined presentation or standalone AI-generated response.

### **Onboarding Scenarios to Be Addressed**

The solution should consider the following onboarding scenarios. These represent practical challenges faced by HR teams and provide the basis for progressive evaluation.  
**1\. Pre-Joining Document Collection and Correction**  
The application should request the documents required for the employee's role and location, validate submissions and identify missing, expired, illegible or incorrect documents. Where a document is rejected, the employee should receive a clear explanation and a request for correction.  
**2\. Employee Profile Creation and IT Provisioning**  
Once required information is approved, the application should populate a structured HR record and initiate provisioning requests for a laptop, email account and role-specific application access.  
**3\. Role-Specific Onboarding and Policy Acknowledgement**  
Onboarding requirements may differ by department, role, grade and location. The application should apply the correct template, present the relevant policies and record each acknowledgement.  
**4\. Joining-Date and Role Changes**  
A joining date may be brought forward or postponed, or a role may change before joining. The application should update dependent tasks, deadlines and owners accordingly.  
**5\. Employee Questions and Delayed Approvals**  
New employees may ask about onboarding steps, documents, first-day arrangements or company policies. Where tasks are blocked because an approval is overdue, the application should escalate to the correct owner.

## **Level 1: Manage Employee Onboarding Requirements**

Suggested time: 0 \- 3 hours  
Objective: Build a working application that creates employee onboarding records, generates role-appropriate checklists, tracks documents and provides grounded AI-assisted guidance.

### **Required Capabilities**

**1\. Employee Data Ingestion**  
Build an application that allows users to upload or select synthetic onboarding information. The application should support the following sample data:

* New-joiner profiles containing name, role, grade, department, location, manager and joining date.  
* Onboarding requirements by role, department and location.  
* Policy documents and onboarding process guides.  
* Department owners for HR, IT, facilities and compliance tasks.

The application should accept CSV, Excel and JSON formats and must validate mandatory fields, date formats and references to managers and departments.  
**2\. Onboarding Checklist Generation**  
Generate a role-appropriate checklist for each new joiner containing required documents, activities, responsible owners and due dates. For example:

| Checklist item | Example rule |
| :---- | :---- |
| Identity and address documents | Required for all employees before joining |
| Education certificates | Required for roles that specify minimum qualifications |
| Background verification | Required before system access is granted |
| Laptop and email provisioning | Requested after the HR record is created |
| Information security acknowledgement | Required before access to production systems |

The application should explain which rule caused each item to be included.  
**3\. Document Submission and Tracking**  
Allow employees to upload documents and track the submission status of each required item. Status values should include Not Submitted, Submitted, Under Review, Accepted and Correction Requested.  
**4\. Document and Record Validation**  
Identify missing mandatory information, incorrect file types, expired documents and incomplete records. Where sample document extraction is used, extracted values such as names and dates should be compared with the employee profile and any mismatch should be highlighted.  
**5\. Conversational Assistance**  
Provide a conversational assistant that answers onboarding questions using approved policy and process information. For example, an employee may ask "Which documents do I need to submit before my joining date?" The answer must be specific to the employee's role and location, include a reference to the source policy and must not invent requirements.  
**6\. Onboarding Progress Dashboard**  
Develop a dashboard that displays completed, pending, blocked and overdue tasks for each new joiner. Users should be able to filter by joining date, department, location and status, and open an employee record to view the full checklist.

### **Expected Submission**

A working onboarding application demonstrating employee data ingestion, role-based checklists, document tracking, validation, grounded conversational assistance and a progress dashboard.