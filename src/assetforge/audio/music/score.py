"""The `Score`: music as data that an agent can write, the composer generates, and MIDI round-trips.

Times are in beats (quarter notes). Melodic notes take a MIDI number or a name (`C4`, `F#3`); drum
notes take a kit-piece name (`kick`, `snare`, `hat_closed`…) or a General MIDI drum number.

```json
{"tempo": 96, "meter": "4/4", "key": "D minor", "bars": 4, "loop": true,
 "tracks": [
   {"name": "lead", "instrument": "flute", "role": "melody",
    "notes": [{"pitch": "D5", "start": 0, "beats": 1}, {"pitch": "F5", "start": 1, "beats": 0.5}]},
   {"name": "beat", "instrument": "drums", "role": "drums",
    "notes": [{"pitch": "kick", "start": 0, "beats": 0.5}, {"pitch": "snare", "start": 1, "beats": 0.5}]}
 ]}
```
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from assetforge.audio.music.theory import Meter, note_number, parse_key, parse_meter
from assetforge.errors import InvalidInputError

Role = Literal["melody", "counter", "harmony", "pad", "arp", "bass", "drums", "percussion", "fx"]
ROLES: tuple[str, ...] = Role.__args__  # type: ignore[attr-defined]


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pitch: int | str = Field(description="MIDI number, note name (C4) or drum piece (kick, snare…)")
    start: float = Field(ge=0, description="Beat where the note starts")
    beats: float = Field(gt=0, le=256, description="Length in beats")
    velocity: float = Field(0.8, ge=0, le=1)


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    instrument: str = Field(description="See `music instruments` (drum kits: drums, drums_808, drums_chip, drums_orchestral)")
    role: Role = "melody"
    gain_db: float = Field(0.0, ge=-48, le=12)
    pan: float = Field(0.0, ge=-1, le=1)
    effects: list[dict[str, Any] | str] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)

    @field_validator("instrument")
    @classmethod
    def _known(cls, value: str) -> str:
        from assetforge.audio.music.instruments import resolve_instrument

        try:
            return resolve_instrument(value)
        except InvalidInputError as exc:
            raise ValueError(exc.message) from exc

    @property
    def is_drums(self) -> bool:
        from assetforge.audio.music.drums import KITS

        return self.instrument in KITS


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    start: float = Field(ge=0, description="Beat")
    beats: float = Field(gt=0)


class Score(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tempo: float = Field(120.0, ge=30, le=300)
    meter: str = "4/4"
    key: str | None = None
    bars: int | None = Field(None, ge=1, le=512, description="Length in bars (default: up to the last note)")
    loop: bool = Field(False, description="Render as a seamless loop of exactly `bars` bars")
    swing: float = Field(0.0, ge=0, le=1)
    space: str | None = Field(None, description="Acoustic space (see `sound spaces`); default room")
    tracks: list[Track] = Field(min_length=1, max_length=24)
    sections: list[Section] = Field(default_factory=list)

    @field_validator("meter")
    @classmethod
    def _meter(cls, value: str) -> str:
        return parse_meter(value).text

    @field_validator("key")
    @classmethod
    def _key(cls, value: str | None) -> str | None:
        return parse_key(value).name if value else None

    @model_validator(mode="after")
    def _pitches(self) -> Score:
        from assetforge.audio.music.drums import drum_piece

        for track in self.tracks:
            for note in track.notes:
                try:
                    if track.is_drums:
                        drum_piece(note.pitch)
                        continue
                    midi = note_number(note.pitch)
                except InvalidInputError as exc:
                    raise ValueError(f"{track.name}: {exc.message}") from exc
                if not 12 <= midi <= 120:
                    raise ValueError(f"{track.name}: note {note.pitch} is outside C0..C9")
        return self

    @property
    def time(self) -> Meter:
        return parse_meter(self.meter)

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.tempo

    @property
    def total_beats(self) -> float:
        if self.bars:
            return self.bars * self.time.beats_per_bar
        end = max((n.start + n.beats for t in self.tracks for n in t.notes), default=self.time.beats_per_bar)
        bar = self.time.beats_per_bar
        return -(-end // bar) * bar

    @property
    def seconds(self) -> float:
        return self.total_beats * self.seconds_per_beat

    def summary(self) -> dict[str, Any]:
        return {"tempo": self.tempo, "meter": self.meter, "key": self.key, "bars": int(self.total_beats //
                self.time.beats_per_bar), "seconds": round(self.seconds, 3), "loop": self.loop,
                "tracks": [{"name": t.name, "instrument": t.instrument, "role": t.role, "notes": len(t.notes)}
                           for t in self.tracks],
                "sections": [s.model_dump() for s in self.sections]}  # fmt: skip

    def melodic_pitches(self) -> list[int]:
        return [note_number(n.pitch) for t in self.tracks if not t.is_drums for n in t.notes]
