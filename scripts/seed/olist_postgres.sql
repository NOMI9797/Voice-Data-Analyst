-- Loads the Olist CSVs (mounted at /data/olist) into PostgreSQL.
-- Idempotent: safe to run again. Column names are kept exactly as in the
-- dataset (including typos like "product_name_lenght") on purpose; the app
-- has to cope with confusing real-world names.
\set ON_ERROR_STOP on
SET client_min_messages = warning;
BEGIN;

DROP TABLE IF EXISTS order_reviews, order_payments, order_items, orders,
  products, product_category_name_translation, sellers, customers, geolocation CASCADE;

CREATE TABLE customers (
  customer_id              varchar(32) PRIMARY KEY,
  customer_unique_id       varchar(32) NOT NULL,
  customer_zip_code_prefix varchar(5),
  customer_city            text,
  customer_state           char(2)
);

CREATE TABLE geolocation (
  geolocation_zip_code_prefix varchar(5),
  geolocation_lat             double precision,
  geolocation_lng             double precision,
  geolocation_city            text,
  geolocation_state           char(2)
);

CREATE TABLE sellers (
  seller_id              varchar(32) PRIMARY KEY,
  seller_zip_code_prefix varchar(5),
  seller_city            text,
  seller_state           char(2)
);

CREATE TABLE product_category_name_translation (
  product_category_name         text PRIMARY KEY,
  product_category_name_english text
);

CREATE TABLE products (
  product_id                 varchar(32) PRIMARY KEY,
  product_category_name      text,
  product_name_lenght        int,
  product_description_lenght int,
  product_photos_qty         int,
  product_weight_g           int,
  product_length_cm          int,
  product_height_cm          int,
  product_width_cm           int
);

CREATE TABLE orders (
  order_id                      varchar(32) PRIMARY KEY,
  customer_id                   varchar(32) REFERENCES customers(customer_id),
  order_status                  text,
  order_purchase_timestamp      timestamp,
  order_approved_at             timestamp,
  order_delivered_carrier_date  timestamp,
  order_delivered_customer_date timestamp,
  order_estimated_delivery_date timestamp
);

CREATE TABLE order_items (
  order_id            varchar(32) REFERENCES orders(order_id),
  order_item_id       int,
  product_id          varchar(32) REFERENCES products(product_id),
  seller_id           varchar(32) REFERENCES sellers(seller_id),
  shipping_limit_date timestamp,
  price               numeric(10,2),
  freight_value       numeric(10,2),
  PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE order_payments (
  order_id             varchar(32) REFERENCES orders(order_id),
  payment_sequential   int,
  payment_type         text,
  payment_installments int,
  payment_value        numeric(10,2),
  PRIMARY KEY (order_id, payment_sequential)
);

-- review_id is NOT unique in the source data, so no primary key here.
CREATE TABLE order_reviews (
  review_id               varchar(32),
  order_id                varchar(32) REFERENCES orders(order_id),
  review_score            int,
  review_comment_title    text,
  review_comment_message  text,
  review_creation_date    timestamp,
  review_answer_timestamp timestamp
);

COPY customers   FROM '/data/olist/olist_customers_dataset.csv'   WITH (FORMAT csv, HEADER true);
COPY geolocation FROM '/data/olist/olist_geolocation_dataset.csv' WITH (FORMAT csv, HEADER true);
COPY sellers     FROM '/data/olist/olist_sellers_dataset.csv'     WITH (FORMAT csv, HEADER true);
COPY product_category_name_translation FROM '/data/olist/product_category_name_translation.csv' WITH (FORMAT csv, HEADER true);
COPY products    FROM '/data/olist/olist_products_dataset.csv'    WITH (FORMAT csv, HEADER true);
COPY orders      FROM '/data/olist/olist_orders_dataset.csv'      WITH (FORMAT csv, HEADER true);
COPY order_items FROM '/data/olist/olist_order_items_dataset.csv' WITH (FORMAT csv, HEADER true);
COPY order_payments FROM '/data/olist/olist_order_payments_dataset.csv' WITH (FORMAT csv, HEADER true);
COPY order_reviews  FROM '/data/olist/olist_order_reviews_dataset.csv'  WITH (FORMAT csv, HEADER true);

CREATE INDEX ON orders (customer_id);
CREATE INDEX ON orders (order_purchase_timestamp);
CREATE INDEX ON order_items (product_id);
CREATE INDEX ON order_items (seller_id);
CREATE INDEX ON order_reviews (order_id);
CREATE INDEX ON products (product_category_name);
CREATE INDEX ON geolocation (geolocation_zip_code_prefix);

GRANT SELECT ON ALL TABLES IN SCHEMA public TO reader;
COMMIT;
ANALYZE;
