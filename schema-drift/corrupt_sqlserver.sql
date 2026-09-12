-- Schema drift corruption — SQL Server
USE clinic;
GO

IF OBJECT_ID('dbo.addresses_staging', 'U') IS NOT NULL DROP TABLE dbo.addresses_staging;
GO
CREATE TABLE addresses_staging (
    customer_id BIGINT PRIMARY KEY,
    street      NVARCHAR(255) NOT NULL,
    city        NVARCHAR(255) NOT NULL,
    state       NVARCHAR(255) NOT NULL,
    zip         NVARCHAR(20) NOT NULL,
    country     NVARCHAR(255) NOT NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('orders') AND name = 'shipping_address')
    ALTER TABLE orders ADD shipping_address NVARCHAR(MAX);
GO

IF OBJECT_ID('dbo.customer_notes', 'U') IS NOT NULL DROP TABLE dbo.customer_notes;
GO
CREATE TABLE customer_notes (
    note_id     BIGINT PRIMARY KEY,
    customer_id NVARCHAR(20) NOT NULL,
    note_text   NVARCHAR(MAX) NOT NULL,
    created_at  DATETIME2 NOT NULL
);
GO
