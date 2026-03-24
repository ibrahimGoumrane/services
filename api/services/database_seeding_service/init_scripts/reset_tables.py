from api.services.database_seeding_service.init_scripts.db_init import get_connection, create_tables

conn = get_connection()
cursor = conn.cursor()

import os

# Get the script directory and construct absolute paths
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
init_files_dir = os.path.join(project_root, "init_files")

files_to_tables = {
    os.path.join(init_files_dir, "domaines_génériques.txt"): ("GgenericDomains", "domain",False),
    os.path.join(init_files_dir, "domaines_notVisited.txt"): ("GnotVisitingDomains", "domain",False),
    os.path.join(init_files_dir, "utilisateurs_génériques.txt"): ("GgenericUsers", "username",False),
    os.path.join(init_files_dir, "site_builder_domains.txt"): ("GsiteBuilderDomains", "domain",True),
}

# --- Fonction pour insérer un domaine dans une table ---
def insert_domain(table_name, domain , column_name):
    # Vérifie si le domaine existe déjà pour éviter les doublons
    cursor.execute(f"SELECT 1 FROM {table_name} WHERE {column_name} = %s", (domain,))
    if cursor.fetchone() is None:
        cursor.execute(f"INSERT INTO {table_name} ({column_name}) VALUES (%s)", (domain,))
        conn.commit()
        print(f"✅ Domaine '{domain}' inséré dans '{table_name}'.")
    else:
        print(f"⚙️ Domaine '{domain}' existe déjà dans '{table_name}' — ignoré.")

def seed_db():
    for filename, (table, column_name , seed) in files_to_tables.items():
        if not seed:
            continue
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                domain = line.strip()  # Supprime les espaces et sauts de ligne
                if domain:
                    insert_domain(table, domain , column_name)
    # ---  Fermeture ---
    cursor.close()
    conn.close()
    print("🔒 Connexion fermée.")

def drop_all_tables():
    """
    Drop all tables from the database
    WARNING: This will delete ALL data!
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    print("⚠️  WARNING: This will DELETE ALL TABLES and DATA!")
    confirm = input("Are you sure you want to continue? (yes/no): ")
    
    if confirm.lower() != 'yes':
        print("❌ Operation cancelled.")
        cursor.close()
        conn.close()
        return False
    
    # List of tables to drop (in reverse order to handle dependencies)
    tables_to_drop = [
        'Gcontact',
        'Gmxrecord',
        'GsiteBuilderDomains',
        'GgenericUsers',
        'GgenericDomains',
        'GnotVisitingDomains'
    ]
    
    print("\n🗑️  Dropping tables...")
    for table in tables_to_drop:
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
            print(f"   ✅ Dropped table '{table}'")
        except Exception as e:
            print(f"   ⚠️  Error dropping table '{table}': {e}")
    
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ All tables dropped successfully!\n")
    return True

def reset_database():
    """
    Complete database reset: drop all tables and recreate them
    """
    print("="*60)
    print("DATABASE RESET UTILITY")
    print("="*60)
    
    # Step 1: Drop all tables
    # if drop_all_tables():
        # Step 2: Recreate tables with new schema
    print("🔨 Recreating tables with updated schema...")
    # create_tables()
    # print("\n✅ Database reset complete!")
    seed_db()
    print("\n✅ Database seeded with initial data!")
    print("="*60)

    # else:
    #     print("Operation aborted.")




if __name__ == "__main__":
    reset_database()
