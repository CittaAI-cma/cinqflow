"""CF-V1-E5-03's grounding — the facts, with the values deliberately absent.

`core.phi.classify` produces the classification; this module turns the part of
it the model is allowed to see into text. The two are separate because the
classification is what the PLATFORM knows and the grounding is what the MODEL
is told, and those must be allowed to differ — they differ here by every value
in the file.

Compare `core.agents.schema_inference.grounding.as_prompt_grounding`, which
DOES send example values: a model asked what type `19360201` is needs to see
`19360201`. A model asked whether a column holds protected data does not need
to see the protected data, and the gateway's scrubber is a safety net rather
than a licence to hand it over.
"""

from __future__ import annotations

from dataclasses import dataclass

from cinqflow.core.agents.phi_detection.graph import PROTECTED_PENDING_REVIEW
from cinqflow.core.patterns import PATTERNS
from cinqflow.core.phi import Classification, ColumnClassification


@dataclass(frozen=True)
class Grounding:
    """What the model is shown. Assembled from names, integers and definitions."""

    classification: Classification

    @property
    def open_questions(self) -> tuple[ColumnClassification, ...]:
        return self.classification.open_questions

    @property
    def needs_no_model(self) -> bool:
        return self.classification.needs_no_model

    def as_prompt_grounding(self) -> str:
        c = self.classification
        lines = [
            f"Feed: {c.feed_id}",
            f"Profile: profile:{c.profile_id} (computed facts — do not contradict these)",
            "",
            "Every column below is ALREADY PROTECTED. You are naming what it is, not "
            "deciding whether to protect it.",
            "",
            "Columns needing a name:",
        ]
        for column in self.open_questions:
            lines.append(f"- source column {column.source_name!r} (position {column.position})")
            lines.append(f"  status: {PROTECTED_PENDING_REVIEW}")
            for fact in column.evidence:
                lines.append(f"  {fact}")

        settled = tuple(col for col in c.columns if col.settled)
        if settled:
            lines += [
                "",
                "Already settled by the glossary or by computation — for context, do not "
                "restate these:",
            ]
            for column in settled:
                what = (
                    column.code_set.label
                    if column.code_set
                    else (column.phi_kind.label if column.phi_kind else "not protected")
                )
                lines.append(
                    f"  {column.source_name}: {what} "
                    f"({'PHI' if column.is_phi else 'not PHI'}, by {column.basis.value})"
                )

        lines += ["", "The value shapes named above, and what they mean:"]
        lines += [
            f"  {p.pattern_id}: {p.label}"
            + (f" — {p.note}" if p.note else "")
            + ("" if p.discriminating else " [SHARED SHAPE: fitting it proves nothing]")
            for p in PATTERNS
        ]
        return "\n".join(lines)

    def as_input(self) -> str:
        """The untrusted fence's contents: the column NAMES, and nothing else.

        A payer's column name is still attacker-controlled text — a file whose
        header row reads `ignore previous instructions` is a file somebody can
        send — so the names go below the fence with the constraints already
        stated above them.
        """
        return "\n".join(column.source_name for column in self.open_questions)


def ground(classification: Classification) -> Grounding:
    return Grounding(classification=classification)
