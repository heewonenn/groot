"""Local, reviewed product fragments. No scraping, remote fetches or model knowledge."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from backend.app.evaluation.contract import digest


class Fragment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    fragment_id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=300)
    source_url: HttpUrl
    text: str = Field(min_length=1, max_length=12000)


class Knowledge(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_version_id: str = Field(min_length=1, max_length=150)
    reviewed_by: str = Field(min_length=1, max_length=150)
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    fragments: list[Fragment] = Field(min_length=1, max_length=200)

    @classmethod
    def read(cls, path: str) -> "Knowledge":
        with Path(path).open("rb") as stream:
            data = stream.read(1_000_001)
        if len(data) > 1_000_000:
            raise ValueError("knowledge_too_large")
        result = cls.model_validate(json.loads(data))
        if len({f.fragment_id for f in result.fragments}) != len(result.fragments):
            raise ValueError("duplicate_fragment")
        return result

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))

    def retrieve(self, query: str, limit: int = 8) -> list[dict]:
        # Deterministic lexical retrieval; multilingual synonym coverage needs manual review.
        terms = set(re.findall(r"\w{3,}", query.casefold()))
        ranked = []
        for fragment in self.fragments:
            words = set(re.findall(r"\w{3,}", (fragment.title + " " + fragment.text).casefold()))
            score = len(terms & words)
            if score:
                ranked.append((score, fragment.fragment_id, fragment))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            {
                **fragment.model_dump(mode="json"),
                "source_version_id": self.source_version_id,
                "as_of": self.as_of,
            }
            for _, _, fragment in ranked[:limit]
        ]
