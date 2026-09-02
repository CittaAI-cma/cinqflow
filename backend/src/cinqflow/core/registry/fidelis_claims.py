"""Fidelis's real claims file inventory — registered, not yet runnable.

    "Fidelis alone has 26 production file patterns (header + line pairs
     across IP/OP/Dental/Professional/Vision, plus Pharmacy, Membership,
     Member-PCP), all full-snapshot every month."
    — docs/DOMAIN.md, from `clientdata/Uploads/2-Claims/Source Details.xlsx`

These 26 are DATA, generated from that source workbook's own rows — not 26
hand-written `FeedRecord(...)` blocks, the same "declare once, build from a
table" shape `core.schema_spec` uses for the platform's own DDL.

EVERY ONE OF THESE STAYS DRAFT, ON PURPOSE. `golden_fidelis.py` has a real
`SchemaContract` and `DqRule`s for the ONE feed (the downstate enrollment
roster) that can actually run today; nobody has authored a contract for any
of these 26 claims patterns yet. Registering them is "onboarding a payer is a
registry row" made honest at the real scale the client's own file inventory
has — visible in the Sources/Feeds screen, not yet Published, because a feed
this platform cannot cast or validate must not be schedulable.

THE SOURCE WORKBOOK'S REGEX IS LOOSE, on purpose here too: its own `.` is
unescaped and its `.*` is meant to mean "some date stamp", not "any single
character, repeated". Rather than guess an exact stamp width nobody has
confirmed, every pattern here accepts any non-empty stamp before the literal
extension — precise enough to be a real, `fullmatch`-checked pattern, honest
about not knowing the exact width until a real sample is profiled.
"""

from __future__ import annotations

from cinqflow.core.registry.feed import FeedRecord

__all__ = ["all_claims_feeds"]

#: (role, workbook prefix, landing subfolder) — one row per claims file TYPE.
#: A header/line pair is two rows, matching the source workbook's own
#: one-row-per-file shape: `FeedRecord.file_pattern` is a single regex, so a
#: "multi-file" feed is two registry rows, never one record with two patterns.
_CLAIM_FILES: tuple[tuple[str, str, str], ...] = (
    ("ip-header", "FidelisCare_Prod_IPClaimHeader_FULL_CINQ{REGION}_", "IP"),
    ("ip-line", "FidelisCare_Prod_IPClaimLine_FULL_CINQ{REGION}_", "IP"),
    ("op-header", "FidelisCare_Prod_OPClaimHeader_FULL_CINQ{REGION}_", "OP"),
    ("op-line", "FidelisCare_Prod_OPClaimLine_FULL_CINQ{REGION}_", "OP"),
    ("dental-header", "FidelisCare_Prod_DentalHeader_FULL_CINQ{REGION}_", "Dental"),
    ("dental-line", "FidelisCare_Prod_DentalLine_FULL_CINQ{REGION}_", "Dental"),
    ("prof-header", "FidelisCare_Prod_ProfHeader_FULL_CINQ{REGION}_", "Prof"),
    ("prof-line", "FidelisCare_Prod_ProfLine_FULL_CINQ{REGION}_", "Prof"),
    ("vision-header", "FidelisCare_Prod_VisionHeader_FULL_CINQ{REGION}_", "Vision"),
    ("vision-line", "FidelisCare_Prod_VisionLine_FULL_CINQ{REGION}_", "Vision"),
    ("pharmacy", "FidelisCare_Prod_Pharmacy_FULL_CINQ{REGION}_", "Pharmacy"),
    ("membership", "FidelisCare_Prod_Membership_FULL_CINQ{REGION}_", "Membership"),
    ("member-pcp", "FidelisCare_Prod_MemberPCP_FULL_CINQ{REGION}_", "Member_pcp"),
)

#: (region slug, workbook's own region token, landing directory)
_REGIONS: tuple[tuple[str, str, str], ...] = (
    ("upstate", "UPSTATE", "fidelis_upstate"),
    ("downstate", "DOWNSTATE", "fidelis_downstate"),
)

#: The four rows the workbook gives real min/max bytes for (decimal MB,
#: matching `golden_fidelis.FEED`'s own convention) — every other row's bound
#: is unset, exactly as the workbook leaves it blank rather than guessed.
_SIZE_BYTES: dict[tuple[str, str], tuple[int, int]] = {
    ("upstate", "ip-header"): (28_000_000, 37_000_000),
    ("upstate", "ip-line"): (17_000_000, 24_000_000),
    ("upstate", "dental-header"): (15_000_000, 21_000_000),
    ("upstate", "dental-line"): (23_000_000, 30_000_000),
}


def _feed_record(
    *, region_slug: str, region_token: str, landing_dir: str, role: str, prefix: str, subfolder: str
) -> FeedRecord:
    stamped_prefix = prefix.format(REGION=region_token)
    sample = f"{stamped_prefix}20260801.txt"
    bounds = _SIZE_BYTES.get((region_slug, role), (None, None))
    return FeedRecord(
        feed_id=f"fidelis-{region_slug}-{role}",
        domain="claims",
        source_system="fidelis",
        file_format="txt",
        landing_path=f"claims/{landing_dir}/{subfolder}",
        file_pattern=rf"{stamped_prefix}.+\.txt",
        schedule_cron="0 3 1 * *",
        sample_filename=sample,
        min_size_bytes=bounds[0],
        max_size_bytes=bounds[1],
    )


def all_claims_feeds() -> tuple[FeedRecord, ...]:
    """All 26 — the real Fidelis claims file inventory, Draft-ready."""
    return tuple(
        _feed_record(
            region_slug=region_slug,
            region_token=region_token,
            landing_dir=landing_dir,
            role=role,
            prefix=prefix,
            subfolder=subfolder,
        )
        for region_slug, region_token, landing_dir in _REGIONS
        for role, prefix, subfolder in _CLAIM_FILES
    )
