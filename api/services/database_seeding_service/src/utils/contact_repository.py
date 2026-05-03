"""Data access layer for contact and related database operations"""

from typing import List, Optional, Tuple

import mysql.connector

from .url_utils import extract_domain


CONTACT_COLUMNS = [
    "email",
    "fullname",
    "fname",
    "lname",
    "url",
    "position",
    "phone",
    "mobile",
    "fax",
    "name",
    "address",
    "city",
    "zip",
    "country",
    "urlcontactform",
    "linkedin",
    "image",
    "mx",
    "emailgeneric",
    "usergeneric",
    "syntaxeemail",
    "sourcefile",
    "CA",
    "activite",
    "whatsapp",
    "facebook",
    "instagram",
    "tiktok",
    "youtube",
    "telegram",
    "calendly",
]

CONTACT_COLUMNS_SQL = ", ".join(CONTACT_COLUMNS)
CONTACT_VALUES_SQL = ", ".join(["%s"] * len(CONTACT_COLUMNS))


# ── Connection ──────────────────────────────────────────────────────────


def get_connection():
    """Establish and return MySQL database connection"""
    return mysql.connector.connect(
        host="169.61.75.4",
        user="finandus_maut672",
        password="(pp5(Km68(0)1vS-",
        database="finandus_maut672",
    )


# ── Generic helpers ─────────────────────────────────────────────────────


def get_one[T](command: str, params: Tuple = ()) -> Optional[T]:
    """Get one record from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(command, params)
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def get_all[T](command: str, params: Tuple = ()) -> List[T]:
    """Get all records from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(command, params)
        rows = cursor.fetchall()
        return [r for r in rows]
    finally:
        cursor.close()
        conn.close()


# ── Reference data ──────────────────────────────────────────────────────


def get_all_not_visiting_domains() -> List[str]:
    """Get all domains that should not be visited."""
    return [r[0] for r in get_all("SELECT domain FROM GnotVisitingDomains")]


def get_all_generic_domains() -> List[str]:
    """Get all generic domain names."""
    return [r[0] for r in get_all("SELECT domain FROM GgenericDomains")]


def get_all_generic_users() -> List[str]:
    """Get all generic username patterns."""
    return [r[0] for r in get_all("SELECT username FROM GgenericUsers")]


def get_all_site_builder_domains() -> List[str]:
    """Get all site builder domain names."""
    return [r[0].lstrip("@") for r in get_all("SELECT domain FROM GsiteBuilderDomains")]


# ── MX records ──────────────────────────────────────────────────────────


def get_all_mxrecords() -> List[str]:
    """Get all MX record root domains."""
    return [r[0] for r in get_all("SELECT rootDomain FROM Gmxrecord")]


def get_mxrecord_by_domain(domain: str) -> Optional[Tuple]:
    """Get MX record for a specific domain."""
    return get_one("SELECT * FROM Gmxrecord WHERE domain=%s", (domain,))


def batch_create_mxrecords(mx_list: List[Tuple[str, str, str]]) -> int:
    """
    Insert multiple MX records in a single batch operation.

    Args:
        mx_list: List of tuples (mx, rootDomain, domain)

    Returns:
        Number of MX records successfully inserted
    """
    if not mx_list:
        return 0

    conn = get_connection()
    cursor = conn.cursor()
    inserted_count = 0

    try:
        mx_hosts = [mx[0] for mx in mx_list]
        placeholders = ",".join(["%s"] * len(mx_hosts))
        cursor.execute(
            f"SELECT mx FROM Gmxrecord WHERE mx IN ({placeholders})", mx_hosts
        )
        existing_mx = set(row[0] for row in cursor.fetchall())

        new_mx = [mx for mx in mx_list if mx[0] not in existing_mx]

        if new_mx:
            cursor.executemany(
                "INSERT INTO Gmxrecord (mx, rootDomain, domain) VALUES (%s, %s, %s)",
                new_mx,
            )
            conn.commit()
            inserted_count = len(new_mx)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return inserted_count


# ── Contacts ────────────────────────────────────────────────────────────


def get_contact_by_domain(url: str) -> Optional[Tuple]:
    """Get one contact row whose stored URL matches the same domain as the given URL."""
    target_domain = extract_domain(url)
    if not target_domain:
        return None

    row = get_one(
        f"""
        SELECT {CONTACT_COLUMNS_SQL}
        FROM Gcontact
        WHERE url IS NOT NULL
          AND url <> ''
          AND (
              LOWER(url) LIKE %s
              OR LOWER(url) LIKE %s
          )
        LIMIT 1
        """,
        (f"http://{target_domain}%", f"https://{target_domain}%"),
    )


    return row


def batch_create_contacts(contacts_list: List[Tuple]) -> Tuple[int, int]:
    """
    Insert or update multiple contacts in a batch operation.

    Deduplicates within batch and merges non-null values into existing records.

    Tuple structure (31 fields):
        0:email(PK), 1:fullname, 2:fname, 3:lname, 4:url, 5:position, 6:phone, 7:mobile,
        8:fax, 9:name(company), 10:address, 11:city, 12:zip, 13:country, 14:urlcontactform,
        15:linkedin, 16:image, 17:mx, 18:emailgeneric, 19:usergeneric, 20:syntaxeemail,
        21:sourcefile, 22:CA, 23:activite, 24:whatsapp, 25:facebook,
        26:instagram, 27:tiktok, 28:youtube, 29:telegram, 30:calendly

    Returns:
        Tuple of (inserted_count, updated_count)
    """
    if not contacts_list:
        return (0, 0)

    conn = get_connection()
    cursor = conn.cursor()
    inserted_count = 0
    updated_count = 0

    try:
        seen = {}
        for contact in contacts_list:
            email = contact[0]
            if not email:
                continue
            if email in seen:
                seen[email] = _merge_contact_data(seen[email], contact)
            else:
                seen[email] = contact
        deduped_contacts = list(seen.values())

        if not deduped_contacts:
            return (0, 0)

        emails = [contact[0] for contact in deduped_contacts]
        placeholders = ",".join(["%s"] * len(emails))
        cursor.execute(
            f"SELECT {CONTACT_COLUMNS_SQL} FROM Gcontact WHERE email IN ({placeholders})",
            emails,
        )
        rows = cursor.fetchall()
        existing_contacts = {row[0]: row for row in rows}

        new_contacts = []
        contacts_to_update = []

        for contact in deduped_contacts:
            email = contact[0]
            if email not in existing_contacts:
                new_contacts.append(contact)
            else:
                existing = existing_contacts[email]
                merged = _merge_contact_data(existing, contact)
                if merged != existing:
                    contacts_to_update.append(merged)

        if new_contacts:
            cursor.executemany(
                f"INSERT INTO Gcontact ({CONTACT_COLUMNS_SQL}) VALUES ({CONTACT_VALUES_SQL})",
                new_contacts,
            )
            inserted_count = len(new_contacts)

        if contacts_to_update:
            cursor.executemany(
                """
                UPDATE Gcontact
                SET fullname=%s, fname=%s, lname=%s, url=%s, position=%s, phone=%s, mobile=%s, fax=%s,
                    name=%s, address=%s, city=%s, zip=%s, country=%s, urlcontactform=%s,
                    linkedin=%s, image=%s, mx=%s, emailgeneric=%s, usergeneric=%s,
                    syntaxeemail=%s, sourcefile=%s, CA=%s, activite=%s,
                    whatsapp=%s, facebook=%s, instagram=%s, tiktok=%s,
                    youtube=%s, telegram=%s, calendly=%s
                WHERE email=%s
                """,
                [
                    (
                        c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8],
                        c[9], c[10], c[11], c[12], c[13], c[14],
                        c[15], c[16], c[17], c[18], c[19],
                        c[20], c[21], c[22], c[23], c[24], c[25],
                        c[26], c[27], c[28], c[29], c[30],
                        c[0],
                    )
                    for c in contacts_to_update
                ],
            )
            updated_count = len(contacts_to_update)

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return (inserted_count, updated_count)


def _merge_contact_data(existing: Tuple, new: Tuple) -> Tuple:
    """Merge two contact tuples, preferring non-null values from new contact."""
    merged = list(existing)
    for i in range(len(existing)):
        if i == 0:
            continue
        new_val = new[i]
        if new_val is not None and new_val != "" and new_val != "None":
            merged[i] = new_val
    return tuple(merged)
