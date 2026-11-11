"""Cardinality preflight before bounded, typed-key inner/left joins."""

from dataclasses import replace

from ..data.limits import enforce_shape
from ..domain.errors import ResourceError, SchemaError


def join_columns(left, right, right_keys):
    output = list(left)
    used_ids = {c.column_id for c in left}
    names = {c.display_name for c in left if not c.is_system}
    for column in right:
        if column.is_system or column.column_id in right_keys:
            continue
        if column.column_id in used_ids:
            raise SchemaError({"reason": "join.column_identity_collision"})
        name = column.display_name
        if name in names:
            name += "_right"
            number = 2
            while name in names:
                name = column.display_name + f"_right_{number}"
                number += 1
        names.add(name)
        used_ids.add(column.column_id)
        output.append(replace(column, display_name=name))
    return tuple(replace(c, ordinal=i) for i, c in enumerate(output))


def execute_join(engine, connection, source, target, columns, step, base_version_id):
    from ..data.schema import INTERNAL_ROW_ID, INTERNAL_ROW_ORDER
    from .engine import StepExecutionResult, _count, _identifier, _table

    p = step.parameters
    catalog = engine.table_access.catalog
    base = catalog.get_version(base_version_id)
    right_version = catalog.get_version(p["right_version_id"])
    if (
        catalog.get_dataset(base.dataset_id).project_id
        != catalog.get_dataset(right_version.dataset_id).project_id
        or right_version.version_id == base_version_id
    ):
        raise SchemaError({"reason": "join.project_or_version"})
    right = engine.table_access.resolve(right_version.version_id)
    output = join_columns(columns, right.columns, p["right_key_ids"])
    left_keys = [
        next((c for c in columns if c.column_id == identifier and not c.is_system), None)
        for identifier in p["left_key_ids"]
    ]
    right_keys = [
        next((c for c in right.columns if c.column_id == identifier and not c.is_system), None)
        for identifier in p["right_key_ids"]
    ]
    if any(c is None for c in (*left_keys, *right_keys)):
        raise SchemaError({"reason": "column_not_found"})
    right_table = f"prep_{10000 + int(target[5:])}"
    connection.execute(
        f"CREATE TEMP TABLE {_table(right_table)} AS SELECT * FROM read_parquet(?)",
        [str(right.parquet_path)],
    )
    try:
        lt = {
            r[0]: r[1]
            for r in connection.execute(f"DESCRIBE SELECT * FROM {_table(source)}").fetchall()
        }
        rt = {
            r[0]: r[1]
            for r in connection.execute(f"DESCRIBE SELECT * FROM {_table(right_table)}").fetchall()
        }
        if any(
            lt[left_key.physical_name] != rt[right_key.physical_name]
            for left_key, right_key in zip(left_keys, right_keys, strict=True)
        ):
            raise SchemaError({"reason": "join.key_type_mismatch"})
        lq = [_identifier(c.physical_name, columns) for c in left_keys]
        rq = [_identifier(c.physical_name, right.columns) for c in right_keys]
        aliases = [f'"k{i}"' for i in range(len(lq))]

        def counts(table, keys):
            projection = ", ".join(f"{k} AS {a}" for k, a in zip(keys, aliases, strict=True))
            where = " AND ".join(f"{k} IS NOT NULL" for k in keys)
            return (
                f"SELECT {projection}, count(*) AS n FROM {_table(table)} WHERE {where} GROUP BY "
                + ", ".join(str(i + 1) for i in range(len(keys)))
            )

        joined = " AND ".join(f"l.{a}=r.{a}" for a in aliases)
        pairs, matched_left, matched_right, left_max, right_max = connection.execute(
            f"WITH l AS ({counts(source, lq)}), r AS ({counts(right_table, rq)}), m AS (SELECT l.n AS ln,r.n AS rn FROM l JOIN r ON {joined}) SELECT COALESCE(sum(ln*rn),0),COALESCE(sum(ln),0),COALESCE(sum(rn),0),(SELECT COALESCE(max(n),0) FROM l),(SELECT COALESCE(max(n),0) FROM r) FROM m"
        ).fetchone()
        if (p["relationship"] in {"one_to_one", "one_to_many"} and left_max > 1) or (
            p["relationship"] in {"one_to_one", "many_to_one"} and right_max > 1
        ):
            raise SchemaError({"reason": "join.cardinality_mismatch"})
        before, right_rows = _count(connection, source), _count(connection, right_table)
        unmatched_left, unmatched_right = (
            before - int(matched_left),
            right_rows - int(matched_right),
        )
        output_rows = int(pairs) + (unmatched_left if p["how"] == "left" else 0)
        if output_rows > p["max_output_rows"] or output_rows > max(1, before) * float(
            p["max_expansion"]
        ):
            raise ResourceError({"reason": "join.expansion_limit", "output_rows": output_rows})
        enforce_shape(output_rows, sum(not c.is_system for c in output))
        on = " AND ".join(f"l.{a}=r.{b}" for a, b in zip(lq, rq, strict=True))
        order_left = _identifier(INTERNAL_ROW_ORDER, columns)
        order_right = _identifier(INTERNAL_ROW_ORDER, right.columns)
        row_left = _identifier(INTERNAL_ROW_ID, columns)
        row_right = _identifier(INTERNAL_ROW_ID, right.columns)
        expressions = []
        left_ids = {c.column_id for c in columns}
        for column in output:
            if column.physical_name == INTERNAL_ROW_ID:
                expressions.append(
                    f"CAST(CAST(md5(concat(l.{row_left},'|',coalesce(r.{row_right},'<UNMATCHED>'),'|',?)) AS UUID) AS VARCHAR) AS {row_left}"
                )
            elif column.physical_name == INTERNAL_ROW_ORDER:
                expressions.append(
                    f"row_number() OVER (ORDER BY l.{order_left},r.{order_right} NULLS LAST)-1 AS {order_left}"
                )
            elif column.column_id in left_ids:
                expressions.append(f"l.{_identifier(column.physical_name, columns)}")
            else:
                expressions.append(f"r.{_identifier(column.physical_name, right.columns)}")
        connection.execute(
            f"CREATE TEMP TABLE {_table(target)} AS SELECT {', '.join(expressions)} FROM {_table(source)} l {'LEFT' if p['how'] == 'left' else 'INNER'} JOIN {_table(right_table)} r ON {on}",
            [right_version.version_id],
        )
        warnings = tuple(
            name
            for name, count in (
                ("join_unmatched_left", unmatched_left),
                ("join_unmatched_right", unmatched_right),
            )
            if count
        )
        result = StepExecutionResult(
            step.step_id,
            output_rows,
            None,
            resolved_values={
                "left_rows": before,
                "right_rows": right_rows,
                "output_rows": output_rows,
                "matched_pairs": int(pairs),
                "unmatched_left": unmatched_left,
                "unmatched_right": unmatched_right,
                "relationship": p["relationship"],
                "null_keys_match": False,
            },
            warnings=warnings,
        )
        return target, output, result, (right_version.version_id,)
    finally:
        connection.execute(f"DROP TABLE {_table(right_table)}")
