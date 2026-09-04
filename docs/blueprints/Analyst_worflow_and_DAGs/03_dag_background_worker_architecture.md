# DAG + Background Worker Architecture for an AI-Powered Data Platform

## Architectural Direction

For an AI-powered data platform, use **DAG/workflow orchestration for lifecycle control** and **background workers for execution**.

AI should be a reasoning capability inside selected workflow steps, not the primary orchestrator.

## Recommended Architecture

```text
ANALYST UX
    ↓
WORKFLOW / DAG LAYER
    ↓
BACKGROUND WORKERS
    ↓
AI INTELLIGENCE
    ↓
DATA / METADATA / KNOWLEDGE
```

### Workflow / DAG Layer

The DAG layer controls:

- Lifecycle
- State
- Dependencies
- Checkpoints
- Retries
- Human approval gates
- Versioning
- Lineage
- Conditional branching
- Resumability
- Re-runs

### Background Workers

Workers execute deterministic or bounded tasks such as:

- CSV/XLSX parsing
- Schema extraction
- Data profiling
- Data-quality checks
- Bronze writing
- Mapping execution
- Transformation
- Validation
- Metadata persistence

### AI Intelligence

AI is invoked where reasoning adds value, for example:

- Semantic interpretation
- Dataset understanding
- Insight generation
- Mapping recommendation
- Anomaly explanation
- Business-context interpretation
- Investigation recommendations

Avoid turning every task into an “agent.”

Use **worker/task/service** terminology for deterministic execution and reserve **AI reasoning** for interpretation and decision-support capabilities.

## Core Execution Pattern

Prefer:

```text
DAG Node
  ↓
Worker Executes
  ↓
AI Reasoning (when required)
  ↓
Structured Result
  ↓
Persist State / Evidence
  ↓
DAG Evaluates Result
  ↓
Next Node
```

Avoid:

```text
Agent Thinks
  ↓
Tool Call
  ↓
Agent Thinks
  ↓
Next Agent
  ↓
Next Tool
```

The first model is more observable, testable, resumable, and controllable.

## Example Ingestion Flow

```text
Upload CSV/XLSX
    ↓
Create Workflow Run
    ↓
Parse File
    ↓
Profile Data
    ↓
AI Understands Dataset
    ↓
Generate Insights
    ↓
Analyst Approval
    ↓
Write Bronze
    ↓
Profile Bronze
    ↓
AI Recommends Mapping
    ↓
Analyst Reviews / Edits Mapping
    ↓
Validate Mapping
    ↓
Execute Mapping
    ↓
Silver / ODS
```

## Human-in-the-Loop

Human decisions should be explicit workflow states.

Example:

```text
MAPPING_PROPOSED
      ↓
ANALYST_REVIEW
      ├── REJECT → AI RE-ANALYSIS → NEW_PROPOSAL
      ├── EDIT   → VALIDATE → CONTINUE
      └── APPROVE → CONTINUE
```

This is much safer than allowing an autonomous agent to make irreversible data decisions.

## Prefer Multiple Workflows Over One Giant DAG

Do not create one enormous workflow spanning the entire platform.

Use meaningful workflow boundaries, such as:

```text
Ingestion Workflow
       ↓
Bronze Understanding Workflow
       ↓
Mapping Workflow
       ↓
Transformation Workflow
       ↓
Validation Workflow
       ↓
ODS Publication Workflow
```

Events or durable state can connect these workflows.

Benefits include:

- Easier recovery
- Smaller failure domains
- Better observability
- Independent retries
- Easier versioning
- Clearer ownership
- Reusable workflow components

## State-First Design

The platform should persist workflow state so work is resumable.

Useful state categories include:

- Dataset state
- Workflow state
- Step state
- AI reasoning result
- Analyst decision
- Mapping version
- Validation result
- Data-quality result
- Evidence
- Lineage
- Audit history

The key principle is:

**The workflow state is the source of truth; the AI is a reasoning capability over that state.**

## Design Principles

### 1. Deterministic Work Where Possible

Use workers for parsing, validation, movement, transformation, and other deterministic operations.

### 2. AI Where Reasoning Is Needed

Use AI for interpretation, recommendations, anomaly explanation, and semantic understanding.

### 3. Human Decisions Are First-Class States

Approvals, edits, and rejections should be persisted and auditable.

### 4. Everything Should Be Resumable

A failed or interrupted step should not force the analyst to restart the entire process.

### 5. Evidence Should Be Persisted

AI recommendations should be connected to the data, rules, statistics, or mappings that support them.

### 6. Make Re-Runs Selective

Analysts and engineers should be able to re-run a specific stage rather than rebuilding everything.

### 7. Keep the AI Stateless Where Practical

Persist AI outputs and workflow state externally so reasoning can be replayed, evaluated, audited, and versioned.

## Final Mental Model

**DAG = Orchestration and Control**

**Worker = Execution**

**AI = Reasoning**

**Analyst = Approval, Correction, and Decision**

**Data Platform = State, Evidence, Lineage, and Governance**

This creates a platform that is intelligent without becoming an uncontrolled autonomous agent system.
