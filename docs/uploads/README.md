# Demo Upload Files

This folder contains demo/sample data files designed for testing and demonstration purposes. These files use minimal mapping fields (3-5 critical fields) to facilitate smooth data ingestion with minimal manual intervention.

## Files Overview

### 1. `demo_adt_simple.csv`
**Purpose**: Simplified ADT (Admission, Discharge, Transfer) data for demonstration

**Fields** (7 fields):
- `PatientID` - Unique patient identifier
- `MemberFirstName` - Patient's first name
- `MemberLastName` - Patient's last name
- `MemberDateOfBirth` - Patient's date of birth
- `AdmissionDate` - Hospital admission date
- `DischargeDate` - Hospital discharge date
- `Facility` - Name of healthcare facility

**Characteristics**:
- 10 sample records
- Clean, standardized data
- No heavy transformation required
- Minimal mapping - direct field alignment to schema
- Realistic date ranges and facility names

---

### 2. `demo_enrollment_basic.csv`
**Purpose**: Basic member enrollment data with multi-payor sources

**Fields** (5 fields):
- `source_system_id` - Unique source system identifier
- `member_first_name` - Member's first name
- `member_last_name` - Member's last name
- `member_dob` - Member's date of birth (YYYY-MM-DD)
- `source_system` - Source payor/system identifier (FIDELIS_NY, MOLINA_NY, CENTENE_GA)

**Characteristics**:
- 10 sample records
- Includes multiple source systems (Fidelis NY, Molina NY, Centene GA)
- Minimal manual intervention required
- Standardized field naming
- Direct mapping to members table

---

### 3. `demo_enrollment_roster.csv`
**Purpose**: Enrollment roster data with contact information

**Fields** (5 fields):
- `member_id` - Unique member identifier
- `member_first_name` - Member's first name
- `member_last_name` - Member's last name
- `member_dob` - Member's date of birth (YYYY-MM-DD)
- `member_phone_number` - Contact phone number

**Characteristics**:
- 10 sample records
- Simplified member identification
- Phone number field included for contact purposes
- No complex hierarchies or nested data
- Easy validation and quality checks

---

## Design Principles

These demo files follow these principles:

1. **Minimal Mapping Fields**: Each file contains only the most critical fields (3-5) required for successful ingestion
2. **No Heavy Manual Intervention**: Data is clean, consistent, and requires no complex transformations
3. **Realistic Data**: All sample data mimics real-world patterns (dates, names, phone formats)
4. **Multiple Scenarios**: Three different files demonstrate various use cases (ADT, basic enrollment, roster)
5. **Standard Formats**: CSV format with consistent date formats (YYYY-MM-DD) and normalized text fields

## Usage

Use these files for:
- **Testing mapping configurations** - Validate field mappings with minimal complexity
- **UI/UX demonstrations** - Show data ingestion workflows with clean sample data
- **Training** - Teach users about data requirements without confusion from complex datasets
- **CI/CD testing** - Use in automated tests with predictable, stable data
- **Performance testing** - Small, manageable datasets for initial performance validation

## Mapping Summary

| File | Mapping Complexity | Manual Intervention | Key Fields |
|------|------------------|-------------------|-----------|
| demo_adt_simple | Low | Minimal | 7 fields |
| demo_enrollment_basic | Very Low | None | 5 fields |
| demo_enrollment_roster | Very Low | None | 5 fields |

