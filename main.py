# ...existing code...
import streamlit as st
import pandas as pd
import plotly.express as px
import json
import os
from pymongo import MongoClient
from pymongo.errors import PyMongoError

st.set_page_config(page_title="Finance Data Visualizer", page_icon="💰", layout="wide")

# MongoDB setup
def get_mongo_uri():
    # First prefer Streamlit secrets, then environment variable
    return st.secrets.get("MONGODB_URI") if hasattr(st, "secrets") and st.secrets.get("MONGODB_URI") else os.environ.get("MONGODB_URI")

_mongo_client = None
def get_mongo_client():
    global _mongo_client
    if _mongo_client is None:
        uri = get_mongo_uri()
        if not uri:
            st.error("MongoDB URI not configured. Set MONGODB_URI in st.secrets or environment variables.")
            return None
        try:
            _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            # force server selection to raise early if bad URI
            _mongo_client.server_info()
        except PyMongoError as e:
            st.error(f"Could not connect to MongoDB: {e}")
            _mongo_client = None
    return _mongo_client

def get_categories_collection():
    client = get_mongo_client()
    if client is None:
        return None
    db = client.get_database("categories_app")  # database name
    return db.get_collection("categories")     # collection name

def load_categories_from_mongo():
    coll = get_categories_collection()
    if coll is None:
        return {"Uncategorized": []}
    try:
        doc = coll.find_one({"_id": "categories_doc"})
        if doc and "data" in doc:
            return doc["data"]
        # initialize default
        default = {"Uncategorized": []}
        coll.replace_one({"_id": "categories_doc"}, {"_id": "categories_doc", "data": default}, upsert=True)
        return default
    except PyMongoError as e:
        st.error(f"Error reading categories from MongoDB: {e}")
        return {"Uncategorized": []}

def save_categories_to_mongo(categories):
    coll = get_categories_collection()
    if coll is None:
        return False
    try:
        coll.replace_one({"_id": "categories_doc"}, {"_id": "categories_doc", "data": categories}, upsert=True)
        return True
    except PyMongoError as e:
        st.error(f"Error saving categories to MongoDB: {e}")
        return False

# replace file-based storage with Mongo-backed functions
if "categories" not in st.session_state:
    st.session_state.categories = load_categories_from_mongo()

def categorize_transaction(df):
    df["Category"] = "Uncategorized"

    for category, keywords in st.session_state.categories.items():
        if category == "Uncategorized" or not keywords:
            continue

        lower_keywords = [keyword.lower().strip() for keyword in keywords]

        for idx, row in df.iterrows():
            details = str(row["Details"]).lower().strip()
            if details in lower_keywords:
                df.at[idx, "Category"] = category

    return df

def load_transactions(file):
    try:
        df = pd.read_csv(file)
        df.columns = [col.strip() for col in df.columns]
        # handle Amount as strings or numeric
        if df["Amount"].dtype == object:
            df["Amount"] = df["Amount"].str.replace(",", "").astype(float)
        else:
            df["Amount"] = df["Amount"].astype(float)
        df["Date"] = pd.to_datetime(df["Date"], format="%d %b %Y")
        df = categorize_transaction(df)
        return df
    except Exception as e:
        st.error(f"Error loading CSV file: {str(e)}")
        return None

def add_keyword_to_category(category, keyword):
    keyword = keyword.strip()
    if not keyword:
        return False
    # update in-memory state
    if category not in st.session_state.categories:
        st.session_state.categories[category] = []
    if keyword not in st.session_state.categories[category]:
        st.session_state.categories[category].append(keyword)
        # persist to mongo using $addToSet for the specific array
        coll = get_categories_collection()
        if coll is not None:
            try:
                coll.update_one({"_id": "categories_doc"}, {"$addToSet": {f"data.{category}": keyword}}, upsert=True)
            except PyMongoError as e:
                st.error(f"Error updating category in MongoDB: {e}")
                # fallback: save whole document
                save_categories_to_mongo(st.session_state.categories)
        else:
            # fallback: save whole document (will show error if no DB)
            save_categories_to_mongo(st.session_state.categories)
        return True
    return False

def main():
    st.title("Finace Data Visualizer")

    uploaded_file = st.file_uploader("Upload your transaction CSV file", type=["csv"])

    if uploaded_file is not None:
        df = load_transactions(uploaded_file)

        if df is not None:
            debits_df = df[df["Debit/Credit"] == "Debit"]
            credits_df = df[df["Debit/Credit"] == "Credit"]

            st.session_state.debits_df = debits_df.copy()

            tab1, tab2 = st.tabs(["Expenses (Debits)", "Payments (Credits)"])
            with tab1:
                
                new_category = st.text_input("Add New Category")
                add_button = st.button("Add Category")

                if add_button and new_category:
                    if new_category not in st.session_state.categories:
                        st.session_state.categories[new_category] = []
                        save_categories_to_mongo(st.session_state.categories)
                        st.rerun()
                    else:
                        st.warning(f"Category '{new_category}' already exists.")

                st.subheader("Your Debits")
                edited_df = st.data_editor(
                    st.session_state.debits_df[["Date", "Details", "Amount", "Category"]],
                    column_config={
                        "Date": st.column_config.DateColumn("Date", format = "MM/DD/YYYY"),
                        "Amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
                        "Category": st.column_config.SelectboxColumn(
                            "Category",
                            options=list(st.session_state.categories.keys()),
                            help="Select or add a category",
                        ),
                    },
                    hide_index=True,
                    use_container_width=True,
                    key="category_editor",
                )

                save_button = st.button("Save Categories", type="primary")
                if save_button:
                    for idx, row in edited_df.iterrows():
                        new_category = row["Category"]
                        if new_category == st.session_state.debits_df.at[idx, "Category"]:
                            continue

                        details = row["Details"]
                        st.session_state.debits_df.at[idx, "Category"] = new_category
                        add_keyword_to_category(new_category, details)

                st.subheader("Expenses by Category")
                category_totals = st.session_state.debits_df.groupby("Category")["Amount"].sum().reset_index()
                category_totals = category_totals.sort_values(by="Amount", ascending=False)

                st.dataframe(
                    category_totals, 
                    column_config={
                        "Amount": st.column_config.NumberColumn("Amount", format="$%.2f")
                    },
                    use_container_width=True,
                    hide_index=True
                )

                fig = px.pie(
                    category_totals, 
                    values="Amount",
                    names="Category",
                    title="Expenses by Category",
                )

                st.plotly_chart(fig, use_container_width=True)


            with tab2:
                st.subheader("Your Credits")
                total_payments = credits_df["Amount"].sum()
                st.metric("Total Credits", f"${total_payments:,.2f}")
                st.write(credits_df)

if __name__ == "__main__":
    main()
# ...existing code...