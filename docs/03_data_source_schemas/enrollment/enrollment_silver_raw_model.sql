-- Members table
CREATE TABLE members (
    source_system_id STRING COMMENT 'Source Identifier Record',
    first_name STRING COMMENT 'Member’s first (given) name',
    last_name STRING COMMENT 'Member’s last (family) name',
    middle_name STRING COMMENT 'Member’s middle name',
    suffix STRING COMMENT 'Name suffix (e.g., Jr, Sr, III)',
    date_of_birth TIMESTAMP COMMENT 'Member’s date of birth',
    sex STRING COMMENT 'Member’s gender/sex',
    race STRING COMMENT 'Member’s race classification',
    ethnicity STRING COMMENT 'Member’s ethnicity classification',
    language STRING COMMENT 'Preferred spoken language',
    location STRING COMMENT 'General location or geographic descriptor of member',
    care_management_program STRING COMMENT 'Care management program the member is enrolled in',
    last_contact TIMESTAMP COMMENT 'Date/time of last contact with member',
    dual_status_code STRING COMMENT 'Indicator of dual eligibility (e.g., Medicare/Medicaid)',
    death_date TIMESTAMP COMMENT 'Member’s date of death',
    record_creation_date TIMESTAMP COMMENT 'Date/time the record was created',
    secure_id STRING COMMENT 'Unique system-generated identifier (GUID)',
    dnc BOOLEAN COMMENT 'Do Not Call indicator',
    lsdeleted BOOLEAN COMMENT 'Deleted flag',
    record_hash STRING COMMENT 'Hash for change detection',
    source_system_id_type STRING COMMENT 'Type of identifier used',
    source_system STRING COMMENT 'Source feed name',
    created_by STRING COMMENT 'Job which created this record',
    created_at TIMESTAMP COMMENT 'Insert timestamp',
    updated_by STRING COMMENT 'Job which updated this record',
    updated_at TIMESTAMP COMMENT 'Update timestamp',
    batch_id INT COMMENT 'Batch lineage identifier',
    CONSTRAINT pk_members PRIMARY KEY (source_system_id, source_system)
)
USING DELTA
PARTITIONED BY (source_system);

-- Members Addresses
CREATE TABLE members_addresses (
    address_type STRING COMMENT 'Type/category of the address',
    address1 STRING COMMENT 'Primary street address line',
    address2 STRING COMMENT 'Secondary address line',
    city STRING COMMENT 'City name',
    state STRING COMMENT 'Two-letter state abbreviation',
    zip STRING COMMENT 'ZIP or ZIP+4 postal code',
    region STRING,
    can_contact BOOLEAN COMMENT 'Flag indicating contact allowed',
    county STRING COMMENT 'County name',
    county_ssa STRING COMMENT 'SSA county code',
    county_fips STRING COMMENT 'FIPS county code',
    zip4 STRING,
    is_zip4 STRING COMMENT 'Additional 4-digit ZIP extension',
    lsdeleted BOOLEAN COMMENT 'Deleted flag',
    record_hash STRING COMMENT 'Hash for change detection',
    source_system_id STRING COMMENT 'Source Unique Identifier',
    source_system STRING COMMENT 'Source feed name',
    created_by STRING COMMENT 'Job which created this record',
    created_at TIMESTAMP COMMENT 'Insert timestamp',
    updated_by STRING COMMENT 'Job which updated this record',
    updated_at TIMESTAMP COMMENT 'Update timestamp',
    batch_id INT COMMENT 'Batch lineage identifier',
    CONSTRAINT pk_members_addresses PRIMARY KEY (address_type, source_system_id, source_system),
    CONSTRAINT fk_members_addresses FOREIGN KEY (source_system_id, source_system) REFERENCES members(source_system_id, source_system)
)
USING DELTA
PARTITIONED BY (source_system);

-- Members Emails
CREATE TABLE members_emails (
    email_type STRING COMMENT 'Email type (Primary, Personal, Secondary)',
    email_address STRING COMMENT 'Email address',
    can_contact BOOLEAN COMMENT 'Flag indicating contact allowed',
    lsdeleted BOOLEAN COMMENT 'Deleted flag',
    record_hash STRING COMMENT 'Hash for change detection',
    source_system_id STRING COMMENT 'Source Unique Identifier',
    source_system STRING COMMENT 'Source feed name',
    created_by STRING COMMENT 'Job which created this record',
    created_at TIMESTAMP COMMENT 'Insert timestamp',
    updated_by STRING COMMENT 'Job which updated this record',
    updated_at TIMESTAMP COMMENT 'Update timestamp',
    batch_id INT COMMENT 'Batch lineage identifier',
    CONSTRAINT pk_members_emails PRIMARY KEY (email_type, source_system_id, source_system),
    CONSTRAINT fk_members_emails FOREIGN KEY (source_system_id, source_system) REFERENCES members(source_system_id, source_system)
)
USING DELTA
PARTITIONED BY (source_system);

-- Members Phones
CREATE TABLE members_phones (
    phone_type STRING COMMENT 'Phone type (Cell, Home, etc.)',
    phone_number STRING COMMENT 'Member’s phone number',
    can_contact BOOLEAN COMMENT 'Flag indicating contact allowed',
    lsdeleted BOOLEAN COMMENT 'Deleted flag',
    record_hash STRING COMMENT 'Hash for change detection',
    source_system_id STRING COMMENT 'Source Unique Identifier',
    source_system STRING COMMENT 'Source feed name',
    created_by STRING COMMENT 'Job which created this record',
    created_at TIMESTAMP COMMENT 'Insert timestamp',
    updated_by STRING COMMENT 'Job which updated this record',
    updated_at TIMESTAMP COMMENT 'Update timestamp',
    batch_id INT COMMENT 'Batch lineage identifier',
    CONSTRAINT pk_members_phones PRIMARY KEY (phone_type, source_system_id, source_system),
    CONSTRAINT fk_members_phones FOREIGN KEY (source_system_id, source_system) REFERENCES members(source_system_id, source_system)
)
USING DELTA
PARTITIONED BY (source_system);

-- Members Enrollment Segments
CREATE TABLE members_enrollment_segments (
    member_group STRING COMMENT 'Facility Name or Practice or Group',
    member_plan STRING COMMENT 'Member Plan',
    member_payor STRING COMMENT 'Member Payor',
    tin_market STRING COMMENT 'PCP State',
    tin_submarket STRING COMMENT 'PCP Upstate/Downstate',
    insurance_id STRING COMMENT 'Insurance ID',
    add_reason STRING COMMENT 'Reason for adding/enrolling member',
    tin STRING COMMENT 'Tax Identification Number',
    pcp_name STRING COMMENT 'Primary Care Provider name',
    pcp_npi STRING COMMENT 'Primary Care Provider NPI',
    pay_to_zip STRING COMMENT 'Pay-to ZIP code',
    lob STRING COMMENT 'Line of Business',
    control_number STRING COMMENT 'Optum-specific control number',
    control_number_pbp STRING COMMENT 'Plan Benefit Package control number',
    lsdeleted BOOLEAN COMMENT 'Deleted flag',
    record_hash STRING COMMENT 'Hash for change detection',
    source_system_id STRING COMMENT 'Source Unique Identifier',
    source_system STRING COMMENT 'Source feed name',
    created_by STRING COMMENT 'Job which created this record',
    created_at TIMESTAMP COMMENT 'Insert timestamp',
    updated_by STRING COMMENT 'Job which updated this record',
    updated_at TIMESTAMP COMMENT 'Update timestamp',
    batch_id INT COMMENT 'Batch lineage identifier',
    CONSTRAINT pk_members_enrollment PRIMARY KEY (member_plan, member_payor, insurance_id, source_system_id, source_system),
    CONSTRAINT fk_members_enrollment FOREIGN KEY (source_system_id, source_system) REFERENCES members(source_system_id, source_system)
)
USING DELTA
PARTITIONED BY (source_system);
