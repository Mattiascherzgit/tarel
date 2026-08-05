"""Deterministic local demo sources for exercising public TAREL workflows."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


class DemoFailure(RuntimeError):
    """A demo creation failure safe to present at the CLI boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DemoCreateResult:
    name: str
    version: int
    database_path: Path
    config_path: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "config_path": str(self.config_path),
            "database_path": str(self.database_path),
            "name": self.name,
            "version": self.version,
        }


def create_retail_demo(
    *,
    path: Path | None = None,
    version: int = 1,
    force: bool = False,
) -> DemoCreateResult:
    """Create the small retail warehouse used by the connector and drift walkthroughs."""
    if version not in {1, 2}:
        raise DemoFailure("invalid_demo_version", "Retail demo version must be 1 or 2.")
    database_path = path or Path.cwd() / ".tarel" / "demos" / "retail-dwh.sqlite"
    database_path = database_path.expanduser()
    config_path = database_path.with_suffix(".toml")
    existing = tuple(candidate for candidate in (database_path, config_path) if candidate.exists())
    if existing and not force:
        raise DemoFailure(
            "demo_exists",
            "Demo already exists; pass --force to replace its database and configuration.",
        )

    database_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=database_path.parent,
        prefix=".retail-dwh-",
        suffix=".sqlite.tmp",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        _create_database(temporary_path, version)
        os.replace(temporary_path, database_path)
        _write_config(config_path, database_path)
    except DemoFailure:
        temporary_path.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        temporary_path.unlink(missing_ok=True)
        raise DemoFailure("demo_create_failed", "Could not create the retail demo.") from exc
    return DemoCreateResult(
        name="retail-dwh",
        version=version,
        database_path=database_path,
        config_path=config_path,
    )


def _create_database(path: Path, version: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA application_id = 1413567052")
        connection.execute(f"PRAGMA user_version = {version}")
        _create_schema(connection, version)
        _insert_dimensions(connection)
        _insert_facts(connection, version)
        connection.commit()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise DemoFailure(
                "invalid_demo_data",
                f"Retail demo has {len(violations)} foreign-key violations.",
            )
    finally:
        connection.close()


def _create_schema(connection: sqlite3.Connection, version: int) -> None:
    territory_field = "TERR_CD" if version == 1 else "TERRITORY_ID"
    net_amount_type = "NUMERIC" if version == 1 else "DECIMAL(18,2)"
    map_primary_key = (
        "PRIMARY KEY (TERR_CD, CHNL_CD)"
        if version == 1
        else "PRIMARY KEY (TERR_CD, CHNL_CD, SALES_ORG)"
    )
    return_amount = ", RETURN_AMT NUMERIC NOT NULL" if version == 1 else ""
    reseller_currency_fk = (
        ", FOREIGN KEY (CURR_CD) REFERENCES D_CURR(CURR_CD)" if version == 1 else ""
    )
    discount_amount = "DISC_AMT NUMERIC NOT NULL DEFAULT 0," if version == 2 else ""
    connection.executescript(
        f"""
        CREATE TABLE D_DATE (
            DATE_KEY INTEGER PRIMARY KEY,
            CAL_YEAR INTEGER NOT NULL,
            CAL_MONTH INTEGER NOT NULL,
            MONTH_NAME TEXT NOT NULL
        );
        CREATE TABLE D_PRD (
            PRD_KEY INTEGER PRIMARY KEY,
            CAT_CD TEXT NOT NULL,
            CAT_NAME TEXT NOT NULL,
            PRD_NAME TEXT NOT NULL,
            ACTIVE_FLG INTEGER NOT NULL
        );
        CREATE TABLE D_CUST (
            CUST_KEY INTEGER PRIMARY KEY,
            COUNTRY_CD TEXT NOT NULL,
            SEG_CD TEXT NOT NULL
        );
        CREATE TABLE D_GEO (
            TERR_ID INTEGER PRIMARY KEY,
            TERR_CD TEXT NOT NULL UNIQUE,
            COUNTRY_CD TEXT NOT NULL,
            REGION_NAME TEXT NOT NULL
        );
        CREATE TABLE D_RSLR (
            RSLR_KEY INTEGER PRIMARY KEY,
            {territory_field} TEXT NOT NULL,
            RSLR_TYPE TEXT NOT NULL
        );
        CREATE TABLE D_CURR (
            CURR_CD TEXT PRIMARY KEY,
            CURR_NAME TEXT NOT NULL
        );
        CREATE TABLE D_CHNL (
            CHNL_CD TEXT PRIMARY KEY,
            CHNL_NAME TEXT NOT NULL
        );
        CREATE TABLE MAP_TERR_CH (
            TERR_CD TEXT NOT NULL,
            CHNL_CD TEXT NOT NULL,
            SALES_ORG TEXT NOT NULL,
            {map_primary_key},
            FOREIGN KEY (CHNL_CD) REFERENCES D_CHNL(CHNL_CD)
        );
        CREATE TABLE F_SLS_01 (
            DOC_ID INTEGER PRIMARY KEY,
            DOC_DT_KEY INTEGER NOT NULL,
            PRD_KEY INTEGER NOT NULL,
            CUST_KEY INTEGER NOT NULL,
            CURR_CD TEXT NOT NULL,
            CHNL_CD TEXT NOT NULL,
            QTY INTEGER NOT NULL,
            NET_AMT {net_amount_type} NOT NULL,
            {discount_amount}
            FOREIGN KEY (DOC_DT_KEY) REFERENCES D_DATE(DATE_KEY),
            FOREIGN KEY (PRD_KEY) REFERENCES D_PRD(PRD_KEY),
            FOREIGN KEY (CUST_KEY) REFERENCES D_CUST(CUST_KEY),
            FOREIGN KEY (CURR_CD) REFERENCES D_CURR(CURR_CD),
            FOREIGN KEY (CHNL_CD) REFERENCES D_CHNL(CHNL_CD)
        );
        CREATE TABLE F_SLS_02 (
            DOC_ID INTEGER PRIMARY KEY,
            DOC_DT_KEY INTEGER NOT NULL,
            PRD_KEY INTEGER NOT NULL,
            RSLR_KEY INTEGER NOT NULL,
            CURR_CD TEXT NOT NULL,
            CHNL_CD TEXT NOT NULL,
            QTY INTEGER NOT NULL,
            NET_AMT {net_amount_type} NOT NULL,
            FOREIGN KEY (DOC_DT_KEY) REFERENCES D_DATE(DATE_KEY),
            FOREIGN KEY (PRD_KEY) REFERENCES D_PRD(PRD_KEY),
            FOREIGN KEY (CHNL_CD) REFERENCES D_CHNL(CHNL_CD)
            {reseller_currency_fk}
        );
        CREATE TABLE F_RET_01 (
            RET_ID INTEGER PRIMARY KEY,
            DOC_ID INTEGER NOT NULL,
            RET_DT_KEY INTEGER NOT NULL,
            PRD_KEY INTEGER NOT NULL,
            RETURN_QTY INTEGER NOT NULL
            {return_amount},
            SRC_CHNL TEXT NOT NULL,
            FOREIGN KEY (RET_DT_KEY) REFERENCES D_DATE(DATE_KEY),
            FOREIGN KEY (PRD_KEY) REFERENCES D_PRD(PRD_KEY),
            FOREIGN KEY (SRC_CHNL) REFERENCES D_CHNL(CHNL_CD)
        );
        CREATE VIEW V_SLS_ALL AS
            SELECT DOC_ID, DOC_DT_KEY, PRD_KEY, CURR_CD, QTY, NET_AMT, CHNL_CD AS SRC_CHNL
            FROM F_SLS_01
            UNION ALL
            SELECT DOC_ID, DOC_DT_KEY, PRD_KEY, CURR_CD, QTY, NET_AMT, CHNL_CD AS SRC_CHNL
            FROM F_SLS_02;
        """
    )


def _insert_dimensions(connection: sqlite3.Connection) -> None:
    months = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    dates = [
        (year * 10000 + month * 100 + 1, year, month, months[month - 1])
        for year in range(2022, 2026)
        for month in range(1, 13)
    ]
    connection.executemany("INSERT INTO D_DATE VALUES (?, ?, ?, ?)", dates)
    connection.executemany(
        "INSERT INTO D_PRD VALUES (?, ?, ?, ?, ?)",
        (
            (1, "BIKE", "Bikes", "Road 100", 1),
            (2, "BIKE", "Bikes", "Touring 200", 1),
            (3, "COMP", "Components", "Wheel Set", 1),
            (4, "COMP", "Components", "Drive Train", 1),
            (5, "ACC", "Accessories", "Helmet", 1),
            (6, "ACC", "Accessories", "Light Set", 1),
            (7, "CLTH", "Clothing", "Jersey", 1),
            (8, "CLTH", "Clothing", "Gloves", 1),
        ),
    )
    countries = ("AT", "DE", "CH", "FR", "US")
    segments = ("CONSUMER", "SMALL_BUSINESS", "ENTERPRISE")
    connection.executemany(
        "INSERT INTO D_CUST VALUES (?, ?, ?)",
        (
            (key, countries[(key - 1) % len(countries)], segments[(key - 1) % len(segments)])
            for key in range(1, 21)
        ),
    )
    geographies = (
        (1, "ALP", "AT", "Alpine"),
        (2, "DACH-N", "DE", "DACH North"),
        (3, "DACH-W", "CH", "DACH West"),
        (4, "EU-W", "FR", "Europe West"),
        (5, "NA", "US", "North America"),
    )
    connection.executemany("INSERT INTO D_GEO VALUES (?, ?, ?, ?)", geographies)
    connection.executemany(
        "INSERT INTO D_RSLR VALUES (?, ?, ?)",
        (
            (key, geographies[(key - 1) % len(geographies)][1], "PARTNER" if key % 2 else "CHAIN")
            for key in range(1, 9)
        ),
    )
    connection.executemany(
        "INSERT INTO D_CURR VALUES (?, ?)",
        (("EUR", "Euro"), ("CHF", "Swiss Franc"), ("USD", "US Dollar")),
    )
    connection.executemany(
        "INSERT INTO D_CHNL VALUES (?, ?)",
        (("WEB", "Internet Sales"), ("RSLR", "Reseller Sales"), ("STORE", "Retail Store")),
    )
    connection.executemany(
        "INSERT INTO MAP_TERR_CH VALUES (?, ?, ?)",
        (
            (territory[1], channel, f"ORG-{territory[0]:02d}")
            for territory in geographies
            for channel in ("WEB", "RSLR")
        ),
    )


def _insert_facts(connection: sqlite3.Connection, version: int) -> None:
    date_keys = [row[0] for row in connection.execute("SELECT DATE_KEY FROM D_DATE")]
    internet_rows = []
    for index in range(1, 121):
        quantity = 1 + index % 4
        amount = round(quantity * (35.0 + (index % 8) * 47.5), 2)
        base = (
            100000 + index,
            date_keys[(index * 3) % len(date_keys)],
            1 + index % 8,
            1 + index % 20,
            "EUR" if index % 5 else "CHF",
            "WEB",
            quantity,
            amount,
        )
        internet_rows.append(base + (round(amount * 0.05, 2),) if version == 2 else base)
    internet_columns = 9 if version == 2 else 8
    connection.executemany(
        f"INSERT INTO F_SLS_01 VALUES ({', '.join('?' for _ in range(internet_columns))})",
        internet_rows,
    )
    reseller_rows = []
    for index in range(1, 97):
        quantity = 2 + index % 7
        amount = round(quantity * (55.0 + (index % 8) * 61.25), 2)
        reseller_rows.append(
            (
                200000 + index,
                date_keys[(index * 5) % len(date_keys)],
                1 + index % 8,
                1 + index % 8,
                ("EUR", "CHF", "USD")[index % 3],
                "RSLR",
                quantity,
                amount,
            )
        )
    connection.executemany("INSERT INTO F_SLS_02 VALUES (?, ?, ?, ?, ?, ?, ?, ?)", reseller_rows)
    returns = []
    for index in range(1, 31):
        quantity = 1 + index % 2
        base = (
            300000 + index,
            (100000 if index % 2 else 200000) + 1 + index * 2,
            date_keys[(index * 7) % len(date_keys)],
            1 + index % 8,
            quantity,
        )
        if version == 1:
            base += (round(quantity * (20.0 + (index % 8) * 17.5), 2),)
        returns.append(base + (("WEB" if index % 2 else "RSLR"),))
    placeholders = ", ".join("?" for _ in range(7 if version == 1 else 6))
    connection.executemany(f"INSERT INTO F_RET_01 VALUES ({placeholders})", returns)


def _write_config(config_path: Path, database_path: Path) -> None:
    url_path = quote(database_path.resolve().as_posix(), safe="/")
    url = f"sqlite:///{url_path}"
    payload = (
        "[sqlite]\n"
        f"url = {json.dumps(url, ensure_ascii=False)}\n"
        'default_database = "TarelRetailDW"\n'
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=config_path.parent,
        prefix=".retail-dwh-",
        suffix=".toml.tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary_path, config_path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
