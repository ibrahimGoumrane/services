"""Data access layer for contact and related database operations"""

from typing import List, Tuple, Dict, Optional, Any
import mysql.connector


def get_connection():
    """Establish and return MySQL database connection"""
    return mysql.connector.connect(
        host="169.61.75.4",
        user="finandus_maut672",
        password="(pp5(Km68(0)1vS-",
        database="finandus_maut672"
    )


# ========================================
# NOT VISITING DOMAINS
# ========================================

def get_all_not_visiting_domains() -> List[str]:
    """Get all domains that should not be visited"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT domain FROM GnotVisitingDomains")
        rows = cursor.fetchall()
        return [r[0] for r in rows]
    finally:
        cursor.close()
        conn.close()


def get_not_visiting_domain(domain: str) -> Optional[Tuple]:
    """Get a specific not-visiting domain record"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM GnotVisitingDomains WHERE domain=%s", (domain,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def create_not_visiting_domain(domain: str) -> None:
    """Create a not-visiting domain record if it doesn't exist"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM GnotVisitingDomains WHERE domain=%s", (domain,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO GnotVisitingDomains (domain) VALUES (%s)", (domain,))
            conn.commit()
    finally:
        cursor.close()
        conn.close()


# ========================================
# GENERIC DOMAINS
# ========================================

def get_all_generic_domains() -> List[str]:
    """Get all generic domain names"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT domain FROM GgenericDomains")
        rows = cursor.fetchall()
        return [r[0] for r in rows]
    finally:
        cursor.close()
        conn.close()


def get_generic_domain(domain: str) -> Optional[Tuple]:
    """Get a specific generic domain record"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM GgenericDomains WHERE domain=%s", (domain,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def create_generic_domain(domain: str) -> None:
    """Create a generic domain record if it doesn't exist"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM GgenericDomains WHERE domain=%s", (domain,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO GgenericDomains (domain) VALUES (%s)", (domain,))
            conn.commit()
    finally:
        cursor.close()
        conn.close()


# ========================================
# GENERIC USERS
# ========================================

def get_all_generic_users() -> List[str]:
    """Get all generic username patterns"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username FROM GgenericUsers")
        rows = cursor.fetchall()
        return [r[0] for r in rows]
    finally:
        cursor.close()
        conn.close()


def get_generic_user(username: str) -> Optional[Tuple]:
    """Get a specific generic user record"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM GgenericUsers WHERE username=%s", (username,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def create_generic_user(username: str) -> None:
    """Create a generic user record if it doesn't exist"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM GgenericUsers WHERE username=%s", (username,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO GgenericUsers (username) VALUES (%s)", (username,))
            conn.commit()
    finally:
        cursor.close()
        conn.close()


# ========================================
# SITE BUILDER DOMAINS
# ========================================

def get_all_site_builder_domains() -> List[str]:
    """Get all site builder domain names"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT domain FROM GsiteBuilderDomains")
        rows = cursor.fetchall()
        return [r[0].lstrip('@') for r in rows]
    finally:
        cursor.close()
        conn.close()


# ========================================
# MX RECORDS
# ========================================

def get_all_mxrecords() -> List[str]:
    """Get all MX record root domains"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT rootDomain FROM Gmxrecord")
        rows = cursor.fetchall()
        return [r[0] for r in rows]
    finally:
        cursor.close()
        conn.close()


def get_mxrecord(mx: str) -> Optional[Tuple]:
    """Get a specific MX record by MX host"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM Gmxrecord WHERE mx=%s", (mx,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def get_mxrecord_by_domain(domain: str) -> Optional[Tuple]:
    """Get MX record for a specific domain"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM Gmxrecord WHERE domain=%s", (domain,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def create_mxrecord(mx: str, root_domain: str, domain: str) -> None:
    """Create an MX record if it doesn't exist"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM Gmxrecord WHERE mx=%s", (mx,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO Gmxrecord (mx, rootDomain, domain) VALUES (%s, %s, %s)",
                (mx, root_domain, domain)
            )
            conn.commit()
    finally:
        cursor.close()
        conn.close()


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
        # Get existing MX records to avoid duplicates
        mx_hosts = [mx[0] for mx in mx_list]
        placeholders = ','.join(['%s'] * len(mx_hosts))
        cursor.execute(f"SELECT mx FROM Gmxrecord WHERE mx IN ({placeholders})", mx_hosts)
        existing_mx = set(row[0] for row in cursor.fetchall())
        
        # Filter out existing MX records
        new_mx = [mx for mx in mx_list if mx[0] not in existing_mx]
        
        if new_mx:
            cursor.executemany(
                "INSERT INTO Gmxrecord (mx, rootDomain, domain) VALUES (%s, %s, %s)",
                new_mx
            )
            conn.commit()
            inserted_count = len(new_mx)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
    
    return inserted_count


# ========================================
# CONTACTS
# ========================================

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
]

CONTACT_COLUMNS_SQL = ", ".join(CONTACT_COLUMNS)
CONTACT_VALUES_SQL = ", ".join(["%s"] * len(CONTACT_COLUMNS))


def get_all_contacts() -> List[Tuple]:
    """Get all contacts from database"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT {CONTACT_COLUMNS_SQL} FROM Gcontact")
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_contact(email: str) -> Optional[Tuple]:
    """Get a specific contact by email"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT {CONTACT_COLUMNS_SQL} FROM Gcontact WHERE email=%s", (email,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def create_contact(
    email: str,
    fullname: Optional[str] = None,
    fname: Optional[str] = None,
    lname: Optional[str] = None,
    url: Optional[str] = None,
    position: Optional[str] = None,
    phone: Optional[str] = None,
    mobile: Optional[str] = None,
    fax: Optional[str] = None,
    name: Optional[str] = None,
    address: Optional[str] = None,
    city: Optional[str] = None,
    zip: Optional[str] = None,
    country: Optional[str] = None,
    urlcontactform: Optional[str] = None,
    linkedin: Optional[str] = None,
    image: Optional[str] = None,
    mx: Optional[str] = None,
    emailgeneric: Optional[bool] = None,
    usergeneric: Optional[bool] = None,
    syntaxeemail: Optional[str] = None,
    sourcefile: Optional[str] = None,
    ca: Optional[str] = None,
    activite: Optional[str] = None,
) -> None:
    """Create a contact record if it doesn't already exist"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM Gcontact WHERE email=%s", (email,))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO Gcontact
                (email, fullname, fname, lname, url, position, phone, mobile, fax, name, address,
                 city, zip, country, urlcontactform, linkedin, image, mx, emailgeneric,
                 usergeneric, syntaxeemail, sourcefile, CA, activite)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                email, fullname, fname, lname, url, position, phone, mobile, fax, name, address,
                city, zip, country, urlcontactform, linkedin, image, mx, emailgeneric,
                usergeneric, syntaxeemail, sourcefile, ca, activite
            ))
            conn.commit()
    finally:
        cursor.close()
        conn.close()


def update_contact(
    email: str,
    fullname: Optional[str] = None,
    fname: Optional[str] = None,
    lname: Optional[str] = None,
    url: Optional[str] = None,
    position: Optional[str] = None,
    phone: Optional[str] = None,
    mobile: Optional[str] = None,
    fax: Optional[str] = None,
    name: Optional[str] = None,
    address: Optional[str] = None,
    city: Optional[str] = None,
    zip: Optional[str] = None,
    country: Optional[str] = None,
    urlcontactform: Optional[str] = None,
    linkedin: Optional[str] = None,
    image: Optional[str] = None,
    mx: Optional[str] = None,
    emailgeneric: Optional[bool] = None,
    usergeneric: Optional[bool] = None,
    syntaxeemail: Optional[str] = None,
    sourcefile: Optional[str] = None,
    ca: Optional[str] = None,
    activite: Optional[str] = None,
) -> None:
    """Update a contact record by email"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE Gcontact
            SET fullname=%s, fname=%s, lname=%s, url=%s, position=%s, phone=%s, mobile=%s, fax=%s,
                name=%s, address=%s, city=%s, zip=%s, country=%s, urlcontactform=%s,
                linkedin=%s, image=%s, mx=%s, emailgeneric=%s, usergeneric=%s,
                syntaxeemail=%s, sourcefile=%s, CA=%s, activite=%s
            WHERE email=%s
        """, (
            fullname, fname, lname, url, position, phone, mobile, fax, name, address, city, zip,
            country, urlcontactform, linkedin, image, mx, emailgeneric, usergeneric,
            syntaxeemail, sourcefile, ca, activite, email
        ))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def drop_all_contacts() -> None:
    """Delete all contacts from database"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM Gcontact")
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def get_contacts_by_emails(emails: List[str]) -> Dict[str, Tuple]:
    """
    Retrieve multiple contacts by their emails.
    
    Args:
        emails: List of email addresses
    
    Returns:
        Dictionary mapping email -> contact_data (as tuple)
    """
    if not emails:
        return {}
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        placeholders = ','.join(['%s'] * len(emails))
        cursor.execute(f"""
            SELECT {CONTACT_COLUMNS_SQL}
            FROM Gcontact WHERE email IN ({placeholders})
        """, emails)
        rows = cursor.fetchall()
        # Create dictionary mapping email -> contact data
        return {row[0]: row for row in rows}
    finally:
        cursor.close()
        conn.close()


def batch_create_contacts(contacts_list: List[Tuple]) -> Tuple[int, int]:
    """
    Insert or update multiple contacts in a batch operation.
    
    Deduplicates within batch and merges non-null values into existing records.
    
    Tuple structure (24 fields):
        0:email(PK), 1:fullname, 2:fname, 3:lname, 4:url, 5:position, 6:phone, 7:mobile,
        8:fax, 9:name(company), 10:address, 11:city, 12:zip, 13:country, 14:urlcontactform,
        15:linkedin, 16:image, 17:mx, 18:emailgeneric, 19:usergeneric, 20:syntaxeemail,
        21:sourcefile, 22:CA, 23:activite
    
    Args:
        contacts_list: List of contact tuples
    
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
        # Deduplicate within batch
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
        
        # Get existing emails
        emails = [contact[0] for contact in deduped_contacts]
        placeholders = ','.join(['%s'] * len(emails))
        cursor.execute(f"""
            SELECT {CONTACT_COLUMNS_SQL}
            FROM Gcontact WHERE email IN ({placeholders})
        """, emails)
        rows = cursor.fetchall()
        existing_contacts = {row[0]: row for row in rows}
        
        # Separate new from existing
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
        
        # Insert new contacts
        if new_contacts:
            cursor.executemany(
                f"INSERT INTO Gcontact ({CONTACT_COLUMNS_SQL}) VALUES ({CONTACT_VALUES_SQL})",
                new_contacts,
            )
            inserted_count = len(new_contacts)
        
        # Update existing contacts
        if contacts_to_update:
            cursor.executemany("""
                UPDATE Gcontact
                SET fullname=%s, fname=%s, lname=%s, url=%s, position=%s, phone=%s, mobile=%s, fax=%s,
                    name=%s, address=%s, city=%s, zip=%s, country=%s, urlcontactform=%s,
                    linkedin=%s, image=%s, mx=%s, emailgeneric=%s, usergeneric=%s,
                    syntaxeemail=%s, sourcefile=%s, CA=%s, activite=%s
                WHERE email=%s
            """, [
                (
                    c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9], c[10], c[11], c[12],
                    c[13], c[14], c[15], c[16], c[17], c[18], c[19], c[20], c[21], c[22], c[23], c[0],
                )
                for c in contacts_to_update
            ])
            updated_count = len(contacts_to_update)
        
        conn.commit()
    
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
    
    return (inserted_count, updated_count)


def batch_update_contacts(contacts_list: List[Tuple]) -> int:
    """
    Update multiple contacts in a batch operation.
    
    Args:
        contacts_list: List of contact tuples
    
    Returns:
        Number of contacts updated
    """
    if not contacts_list:
        return 0
    
    conn = get_connection()
    cursor = conn.cursor()
    updated_count = 0
    
    try:
        cursor.executemany("""
            UPDATE Gcontact
            SET fullname=%s, fname=%s, lname=%s, url=%s, position=%s, phone=%s, mobile=%s, fax=%s,
                name=%s, address=%s, city=%s, zip=%s, country=%s, urlcontactform=%s,
                linkedin=%s, image=%s, mx=%s, emailgeneric=%s, usergeneric=%s,
                syntaxeemail=%s, sourcefile=%s, CA=%s, activite=%s
            WHERE email=%s
        """, [
            (
                c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9], c[10], c[11], c[12],
                c[13], c[14], c[15], c[16], c[17], c[18], c[19], c[20], c[21], c[22], c[23], c[0],
            )
            for c in contacts_list
        ])
        conn.commit()
        updated_count = cursor.rowcount
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()
    
    return updated_count


def _merge_contact_data(existing: Tuple, new: Tuple) -> Tuple:
    """
    Merge two contact tuples, preferring non-null values from new contact.
    
    Args:
        existing: Existing contact data tuple
        new: New contact data tuple
    
    Returns:
        Merged contact tuple
    """
    merged = list(existing)
    
    for i in range(len(existing)):
        # Skip email field (index 0) - it's the primary key
        if i == 0:
            continue
        
        new_val = new[i]
        has_new_value = new_val is not None and new_val != '' and new_val != 'None'
        
        if has_new_value:
            merged[i] = new_val
    
    return tuple(merged)
