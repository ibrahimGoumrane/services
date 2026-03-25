import mysql.connector


# Connexion à MySQL
def get_connection():
    return mysql.connector.connect(
        host="169.61.75.4",
        user="finandus_maut672",
        password="(pp5(Km68(0)1vS-",
        database="finandus_maut672"
    )
conn = get_connection()
cursor = conn.cursor()

print("✅ Connexion réussie !")

# # --- Création et sélection de la base ---
# cursor.execute("CREATE DATABASE IF NOT EXISTS contact_db")
# cursor.execute("USE contact_db")

# --- Fonction utilitaire pour vérifier l'existence d'une table ---
def table_exists(table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None

# --- Création des tables si elles n'existent pas ---
tables = {
    "notVisitingDomains": """
        CREATE TABLE GnotVisitingDomains (
            domain VARCHAR(255) PRIMARY KEY
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "GenericDomains": """
        CREATE TABLE GgenericDomains (
            domain VARCHAR(255) PRIMARY KEY
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "GenericUsers": """
        CREATE TABLE GgenericUsers (
            username VARCHAR(255) PRIMARY KEY
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "Mxrecord": """
        CREATE TABLE Gmxrecord (
            mx VARCHAR(255),
            rootDomain VARCHAR(255),
            domain VARCHAR(255)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "Contact": """
        CREATE TABLE Gcontact (
            email VARCHAR(255) PRIMARY KEY,
            fname VARCHAR(255),
            lname VARCHAR(255),
            url VARCHAR(255),
            position VARCHAR(255),
            phone VARCHAR(255),
            mobile VARCHAR(255),
            fax VARCHAR(255),
            company VARCHAR(255),
            address VARCHAR(255),
            city VARCHAR(255),
            zip VARCHAR(255),
            country VARCHAR(255),
            urlcontactform VARCHAR(255),
            linkedin VARCHAR(255),
            image VARCHAR(255),
            mx VARCHAR(255),
            emailgeneric VARCHAR(255),
            usergeneric VARCHAR(255),
            syntaxeemail VARCHAR(255),
            sourcefile VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "SiteBuilderDomains": """
        CREATE TABLE GsiteBuilderDomains (
            domain VARCHAR(255) PRIMARY KEY
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
}


def create_tables():
    for name, query in tables.items():
        # Extract the actual table name from the CREATE TABLE statement
        actual_table_name = query.split("CREATE TABLE ")[1].split(" (")[0].strip()
        if not table_exists(actual_table_name):
            cursor.execute(query)
            print(f"✅ Table '{actual_table_name}' créée.")
        else:
            print(f"⚙️ Table '{actual_table_name}' existe déjà — ignorée.")

    # ---  Fermeture ---
    cursor.close()
    conn.close()
    print("🔒 Connexion fermée.")

if __name__ == "__main__":
    create_tables()