# Persona-Based Data View for Data Analysts

## Objective

A data analyst should not have to read every row and column to understand a dataset.

For example:

**30,000 rows × 100 columns = 3,000,000 cells**

The platform should not reduce or hide the underlying data. It should reduce the **cognitive load** required to understand it.

## Core Concept

**Raw Data → AI Understanding → Data Compression → Persona + Question → Relevant View → Insight → Evidence → Records**

The full dataset remains available, but the default experience presents the information that matters most.

## Default Analyst View

The first screen should emphasize:

1. Dataset summary
2. Business entities
3. Important fields
4. Data-quality signals
5. Key metrics
6. Patterns and anomalies
7. AI-generated insights
8. Recommended investigation paths or actions

Do not lead with thousands of raw rows.

Technical metadata, low-value fields, repeated values, and internal system attributes should remain available through progressive disclosure rather than dominating the primary view.

## Column Compression

A dataset with 100 columns should be classified into semantic groups such as:

- Identifiers
- Measures
- Dimensions
- Dates
- Business attributes
- Technical fields
- Derived fields

The AI should prioritize a smaller set of **Recommended Fields** based on:

- The business question
- Important entities
- Relevant metrics
- Relationships between fields
- Data quality
- Analytical relevance

The analyst can still expand to view the complete schema.

## Row Compression

Instead of requiring the analyst to inspect 30,000 rows, the platform should surface:

- Summary statistics
- Aggregations
- Distributions
- Representative samples
- Outliers
- Anomalies
- Segment comparisons
- Time trends
- Evidence records

The purpose is not to replace the records. It is to make the important records discoverable.

## Progressive Disclosure

### Level 1 — Summary
**What is this data?**

Show what the dataset contains, its size, entities, time coverage, and major quality indicators.

### Level 2 — Insight
**What is important?**

Show trends, unusual patterns, changes, segments, and potential business findings.

### Level 3 — Evidence
**Why should I believe this?**

Show calculations, supporting distributions, comparisons, contributing dimensions, and relevant evidence.

### Level 4 — Records
**Show me the actual data.**

Allow drill-down into raw records when the analyst needs validation or investigation.

## Persona-Based Prioritization

### Business Analyst

Prioritize:

- KPIs
- Trends
- Segments
- Business dimensions
- Exceptions
- Insights
- Recommended actions

### Data Analyst

Prioritize:

- Metrics
- Relationships
- Distributions
- Missingness
- Outliers
- Correlations
- Supporting evidence

### Data Engineer

Prioritize:

- Schema
- Lineage
- Data types
- Duplicates
- Nulls
- Constraints
- Pipeline and ingestion issues

The same underlying dataset can therefore produce different default views depending on the user's persona.

## Question-Aware View

The business question should dynamically determine what the analyst sees first.

For example:

**Question:**  
Why did hospital readmissions increase?

The platform should prioritize fields such as:

- Readmission rate
- Time period
- Facility
- Provider
- Diagnosis or condition
- Patient cohort
- Supporting utilization and outcome measures

Unrelated technical fields should move into lower-priority exploration areas.

## AI as a Compression Layer

The AI layer should:

**Profile → Classify → Summarize → Prioritize → Detect Anomalies → Explain Patterns → Recommend Investigation Paths**

This creates a semantic layer between the raw data and the analyst experience.

## Analyst Control

The analyst must be able to:

- Explore
- Filter
- Expand
- Drill down
- Show evidence
- View records
- Ask AI questions
- Change prioritized fields

AI should accelerate understanding, not remove analyst control.

## Core UX Model

**Summary First → Relevant First → Semantic Before Technical → Insight Before Records → Progressive Disclosure → Evidence Always Available → Persona + Context Driven → Raw Data Remains Accessible**

## Final Mental Model

A good platform turns:

**30,000 × 100 raw cells**

into:

**AI understanding → Relevant representation → Insight → Evidence → Data**

The platform should not make the analyst read the data.

**It should help the analyst understand the data.**
