-- Phase 1: partition `events` by month — SQL Server
-- No FK/partitioning restriction here (unlike MySQL), so events.customer_id's FK
-- is preserved. PK must include the partition key, so it becomes composite
-- (event_id, event_time), same as the other two engines.

USE clinic;
GO

CREATE PARTITION FUNCTION pf_events_monthly (DATETIME2)
AS RANGE RIGHT FOR VALUES (

    '2023-10-01', '2023-11-01', '2023-12-01', '2024-01-01', '2024-02-01', '2024-03-01', '2024-04-01', '2024-05-01', '2024-06-01', '2024-07-01', '2024-08-01', '2024-09-01', '2024-10-01', '2024-11-01', '2024-12-01', '2025-01-01', '2025-02-01', '2025-03-01', '2025-04-01', '2025-05-01', '2025-06-01', '2025-07-01', '2025-08-01', '2025-09-01', '2025-10-01', '2025-11-01', '2025-12-01', '2026-01-01', '2026-02-01', '2026-03-01', '2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01', '2026-09-01'
);
GO

CREATE PARTITION SCHEME ps_events_monthly
AS PARTITION pf_events_monthly ALL TO ([PRIMARY]);
GO

EXEC sp_rename 'events', 'events_old';
GO

CREATE TABLE events (
    event_id        BIGINT IDENTITY(1,1) NOT NULL,
    customer_id     BIGINT NOT NULL FOREIGN KEY REFERENCES dbo.customers(customer_id),
    event_type      NVARCHAR(50) NOT NULL,
    event_time      DATETIME2 NOT NULL,
    metadata        NVARCHAR(MAX),
    CONSTRAINT PK_events PRIMARY KEY (event_id, event_time)
) ON ps_events_monthly(event_time);
GO

SET IDENTITY_INSERT events ON;
GO

INSERT INTO events (event_id, customer_id, event_type, event_time, metadata)
SELECT event_id, customer_id, event_type, event_time, metadata FROM events_old;
GO

SET IDENTITY_INSERT events OFF;
GO

DBCC CHECKIDENT ('events', RESEED);
GO

DROP TABLE events_old;
GO
