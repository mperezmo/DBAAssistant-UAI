/*
    DistribuidoraDemo · Backup en el SQL Server del docker, para moverla a otra instancia.
    Deja el archivo en /var/opt/mssql/data/DistribuidoraDemo.bak (dentro del contenedor)
    y verifica que se pueda leer.
*/
SET NOCOUNT ON;
BACKUP DATABASE DistribuidoraDemo
    TO DISK = N'/var/opt/mssql/data/DistribuidoraDemo.bak'
    WITH COMPRESSION, INIT, CHECKSUM, STATS = 25;
RESTORE VERIFYONLY FROM DISK = N'/var/opt/mssql/data/DistribuidoraDemo.bak' WITH CHECKSUM;
GO
