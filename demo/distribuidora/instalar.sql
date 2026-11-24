/*
    DistribuidoraDemo · Instalación completa (estructura, datos y problemas de DBA).

    Ejecutar con sqlcmd desde esta carpeta (los :r usan rutas relativas):
        sqlcmd -C -S <servidor> -U <usuario> -P <clave> -i instalar.sql
    Tarda alrededor de un minuto. Borra y vuelve a crear la base DistribuidoraDemo.
*/
:on error exit
:r 01_estructura.sql
:r 02_datos.sql
:r 03_problemas_dba.sql
