from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PublishJob:
    video_path: Path
    video_url: str
    caption: str
    title: str


@dataclass
class PostResult:
    platform: str
    post_id: str
    url: str


class PublishError(Exception):
    pass
