-- ================================================================
-- DatabaseSetup.sql
-- Run this script in SQL Server Management Studio ONCE before
-- starting the application for the first time.
--
-- Steps:
--   1. Open SQL Server Management Studio
--   2. Connect to YOUR_SERVER
--   3. Open this file (File → Open → File...)
--   4. Press F5 to execute
-- ================================================================

-- Create the database if it does not already exist
IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = 'InventoryDB')
BEGIN
    CREATE DATABASE InventoryDB;
END
GO

USE InventoryDB;
GO

-- ── Table: Categories ────────────────────────────────────────────
IF NOT EXISTS (
    SELECT * FROM sys.objects
    WHERE object_id = OBJECT_ID(N'Categories') AND type = N'U'
)
BEGIN
    CREATE TABLE Categories (
        Id          INT           IDENTITY(1,1) PRIMARY KEY,
        Name        NVARCHAR(100) NOT NULL,
        Description NVARCHAR(500) NULL
    );
END
GO

-- ── Table: Products ──────────────────────────────────────────────
IF NOT EXISTS (
    SELECT * FROM sys.objects
    WHERE object_id = OBJECT_ID(N'Products') AND type = N'U'
)
BEGIN
    CREATE TABLE Products (
        Id          INT             IDENTITY(1,1) PRIMARY KEY,
        Name        NVARCHAR(200)   NOT NULL,
        Description NVARCHAR(1000)  NULL,
        Price       DECIMAL(18, 2)  NOT NULL,
        Stock       INT             NOT NULL DEFAULT 0,
        CategoryId  INT             NOT NULL,
        CreatedAt   DATETIME        NOT NULL DEFAULT GETDATE(),

        CONSTRAINT FK_Products_Categories
            FOREIGN KEY (CategoryId) REFERENCES Categories(Id)
    );
END
GO

-- ── Seed data ────────────────────────────────────────────────────
-- Only inserts if the tables are empty (safe to run multiple times)

IF NOT EXISTS (SELECT 1 FROM Categories)
BEGIN
    INSERT INTO Categories (Name, Description) VALUES
        ('Electronics', 'Electronic devices and accessories'),
        ('Furniture',   'Home and office furniture'),
        ('Clothing',    'Apparel and fashion items');
END
GO

IF NOT EXISTS (SELECT 1 FROM Products)
BEGIN
    INSERT INTO Products (Name, Description, Price, Stock, CategoryId, CreatedAt)
    VALUES
        ('Laptop Pro 15',  'High-performance business laptop with 16GB RAM', 1299.99, 25,  1, GETDATE()),
        ('Wireless Mouse', 'Ergonomic wireless optical mouse',                  29.99, 100, 1, GETDATE()),
        ('Office Chair',   'Adjustable ergonomic office chair',                349.00, 15,  2, GETDATE()),
        ('Standing Desk',  'Height-adjustable standing desk 140x70cm',         599.00, 8,   2, GETDATE()),
        ('T-Shirt Basic',  '100% cotton crew-neck basic t-shirt',               19.99, 200, 3, GETDATE());
END
GO

PRINT 'DatabaseSetup.sql completed successfully.';
