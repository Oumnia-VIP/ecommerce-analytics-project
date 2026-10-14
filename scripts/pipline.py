import pandas as pd
import numpy as np
import uuid
import time
import joblib
from pathlib import Path
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
model = joblib.load(PROJECT_ROOT / "models" / "late_delivery_model.pkl")

engine = create_engine('postgresql://postgres:Iftahyasmsm2003@localhost:5432/ecommerce_analytics')

DELIVERY_DELAY_SECONDS = 30
REVIEW_DELAY_SECONDS = 20


def get_reference_values():
    states = pd.read_sql("SELECT DISTINCT customer_state FROM customers", engine)['customer_state'].tolist()
    categories = pd.read_sql(
        "SELECT DISTINCT product_category_name_english FROM products WHERE product_category_name_english IS NOT NULL",
        engine
    )['product_category_name_english'].tolist()
    return states, categories


def create_and_score_order(states, categories):
    new_order = pd.DataFrame({
        'order_id': [str(uuid.uuid4())],
        'customer_state': np.random.choice(states, 1),
        'product_category_name_english': np.random.choice(categories, 1),
        'order_value': np.round(np.random.uniform(30, 500, 1), 2),
        'num_items': np.random.randint(1, 4, 1),
        'freight_value': np.round(np.random.uniform(10, 80, 1), 2),
        'order_month': np.random.randint(1, 13, 1)
    })
    new_order.to_sql('new_orders', engine, if_exists='append', index=False)

    features = ['customer_state', 'product_category_name_english', 'order_value',
                'num_items', 'freight_value', 'order_month']
    X_new = new_order[features]
    prediction = pd.DataFrame({
        'order_id': new_order['order_id'],
        'predicted_late': model.predict(X_new),
        'late_probability': model.predict_proba(X_new)[:, 1].round(4)
    })
    prediction.to_sql('order_risk_predictions', engine, if_exists='append', index=False)
    print(f"Created + scored order {new_order['order_id'].iloc[0]} (risk={prediction['late_probability'].iloc[0]})")


def _simulate_actual_outcome(p):
    """Take a predicted probability, add noise, clamp to [0,1], then flip a weighted coin ONCE."""
    noisy_p = p + np.random.uniform(-0.15, 0.15)
    noisy_p = min(max(noisy_p, 0.0), 1.0)
    return 1 if np.random.random() < noisy_p else 0


def resolve_outcomes():
    query = f"""
    SELECT n.order_id, p.late_probability
    FROM new_orders n
    JOIN order_risk_predictions p ON n.order_id = p.order_id
    LEFT JOIN new_order_outcomes o ON n.order_id = o.order_id
    WHERE o.order_id IS NULL
      AND n.created_at < NOW() - INTERVAL '{DELIVERY_DELAY_SECONDS} seconds'
    """
    pending = pd.read_sql(query, engine)
    if len(pending) == 0:
        return

    pending['actual_is_late'] = pending['late_probability'].apply(_simulate_actual_outcome)
    outcomes = pending[['order_id', 'actual_is_late']]
    outcomes.to_sql('new_order_outcomes', engine, if_exists='append', index=False)
    print(f"Resolved outcomes for {len(outcomes)} order(s)")


def _sample_review_score(is_late):
    if is_late == 1:
        return np.random.choice([1, 2, 3, 4, 5], p=[0.35, 0.30, 0.20, 0.10, 0.05])
    else:
        return np.random.choice([1, 2, 3, 4, 5], p=[0.02, 0.03, 0.10, 0.25, 0.60])


def generate_reviews():
    query = f"""
    SELECT o.order_id, o.actual_is_late
    FROM new_order_outcomes o
    LEFT JOIN new_order_reviews r ON o.order_id = r.order_id
    WHERE r.order_id IS NULL
      AND o.delivered_at < NOW() - INTERVAL '{REVIEW_DELAY_SECONDS} seconds'
    """
    pending = pd.read_sql(query, engine)
    if len(pending) == 0:
        return

    pending['review_score'] = pending['actual_is_late'].apply(_sample_review_score)
    reviews = pending[['order_id', 'review_score']]
    reviews.to_sql('new_order_reviews', engine, if_exists='append', index=False)
    print(f"Generated reviews for {len(reviews)} order(s)")


if __name__ == "__main__":
    states, categories = get_reference_values()
    print("Pipeline running. Press Ctrl+C to stop.")
    while True:
        try:
            create_and_score_order(states, categories)
            resolve_outcomes()
            generate_reviews()
        except Exception as e:
            print(f"Error in loop: {e}")
        time.sleep(15)