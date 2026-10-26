"""Discovery helpers: visual similarity, near-duplicates and re-indexing (`similar`, `duplicates`, `reindex`)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from assetforge.cli_common import JsonOpt, session
from assetforge.core import visual
from assetforge.core.models import SearchHit
from assetforge.output import render_assets


def register(app: typer.Typer) -> None:
    @app.command()
    def similar(
        ctx: typer.Context,
        ref: Annotated[str, typer.Argument(help="Asset to compare against (ID, key or variant).")],
        limit: Annotated[int, typer.Option(help="Maximum results.")] = 10,
        include_rejected: Annotated[bool, typer.Option("--include-rejected", help="Also show rejected/archived.")] = False,
        json_: JsonOpt = False,
    ) -> None:
        """Assets that look like REF (shape, layout and colour mix), best first. `distance` ≤ 6 is near-identical."""
        with session(ctx, json_) as s:
            where = "1" if include_rejected else "a.status IN ('approved', 'candidate')"
            ranked = visual.similar(s.lib, ref, limit=limit, where=where)
            summaries = {a.id: a for a in s.lib.summaries([i for i, _, _ in ranked])}
            hits = [SearchHit(**summaries[i].model_dump(), score=score) for i, score, _ in ranked]
            results = [{**h.model_dump(mode="json"), "distance": d} for h, (_, _, d) in zip(hits, ranked, strict=True)]
            s.out.emit({"ref": s.lib.resolve_asset_id(ref), "count": len(results), "results": results},
                       lambda c: render_assets(c, hits, title=f"Looks like {ref}"))  # fmt: skip

    @app.command()
    def duplicates(
        ctx: typer.Context,
        threshold: Annotated[int, typer.Option(help="Max perceptual-hash distance (of 64) to call two images the same.")]
        = visual.NEAR_DUPLICATE_BITS,
        include_rejected: Annotated[bool, typer.Option("--include-rejected")] = False,
        json_: JsonOpt = False,
    ) -> None:
        """Groups of assets whose images are visually the same picture (exact copies are already merged on import)."""
        with session(ctx, json_) as s:
            groups = visual.duplicate_groups(s.lib, threshold=threshold, include_rejected=include_rejected)

            def human(c: Any) -> None:
                if not groups:
                    c.print("[green]No near-duplicates.[/green]")
                for group in groups:
                    c.print(" ≈ ".join(f"{g['asset_id']} ({g['name']}, {g['status']})" for g in group))

            s.out.emit({"threshold": threshold, "count": len(groups), "groups": groups}, human)

    @app.command()
    def reindex(
        ctx: typer.Context,
        visual_: Annotated[bool, typer.Option("--visual/--text-only", help="Also compute missing colour/hash features.")]
        = True,
        json_: JsonOpt = False,
    ) -> None:
        """Fill in visual features for older files and rebuild the search index. Safe to run any time."""
        with session(ctx, json_) as s:
            missing_sql = "SELECT sha256 FROM asset_files WHERE dhash IS NULL AND kind IN ('image', 'vector')"
            missing = [r[0] for r in s.lib.conn.execute(missing_sql)] if visual_ else []
            for sha in missing:
                visual.ensure_features(s.lib, sha)
            computed = len(missing) - len(s.lib.conn.execute(missing_sql).fetchall()) if missing else 0
            indexed = s.lib.rebuild_fts()
            data = {"visual_features_computed": computed, "assets_indexed": indexed}
            s.out.emit({"reindex": data},
                       lambda c: c.print(f"Indexed {indexed} asset(s); computed visual features for {computed} file(s)."))  # fmt: skip
