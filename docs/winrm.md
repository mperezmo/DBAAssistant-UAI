# Preparar un servidor Windows para WinRM

DBA Assistant usa WinRM para consultar e iniciar el servicio de SQL Server cuando el
motor no acepta conexiones. La conexión usa NTLM (puerto 5985, o 5986 con HTTPS).
Todos los comandos se ejecutan en PowerShell como administrador, en el servidor Windows.

## 1. Habilitar WinRM

```powershell
winrm quickconfig            # con la red en perfil "Pública": winrm quickconfig -force
winrm get winrm/config/service/auth   # debe indicar Negotiate = true
```

## 2. Cuenta de servicio local

```powershell
net user dbaassistant * /add /expires:never /logonpasswordchg:no
net localgroup "Usuarios de administración remota" dbaassistant /add
net localgroup Administradores dbaassistant /add
# Sin esta clave Windows recorta los permisos de administrador de las cuentas
# locales en conexiones remotas y WinRM responde "Acceso denegado".
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System /v LocalAccountTokenFilterPolicy /t REG_DWORD /d 1 /f
Restart-Service WinRM
```

En Windows en inglés los grupos son `Remote Management Users` y `Administrators`.
En la app el usuario se carga como `NOMBRE-DEL-EQUIPO\dbaassistant` (el comando
`hostname` devuelve el nombre del equipo).

## 3. Verificar

```powershell
$cred = Get-Credential NOMBRE-DEL-EQUIPO\dbaassistant
Invoke-Command -ComputerName localhost -Authentication Negotiate -Credential $cred -ScriptBlock { whoami }
```

Luego, en **Conexiones**, el botón **Probar WinRM** lista los servicios de SQL Server.

## Diagnóstico

Ante el error `WRM_000` la causa queda en el log del backend
(`docker compose logs backend | findstr WRM_000`):

| Causa en el log | Solución |
|---|---|
| `ConnectTimeout` | El firewall descarta la conexión: `Set-NetFirewallRule -Name WINRM-HTTP-In-TCP -RemoteAddress Any` |
| `ConnectionError` (refused) | WinRM no está escuchando: `winrm quickconfig` |
| `InvalidCredentialsError` | Contraseña, grupos o `LocalAccountTokenFilterPolicy` (paso 2) |

Los logins fallidos se ven en el registro de Seguridad (evento 4625) si la auditoría
está activa: `auditpol /set /subcategory:"Inicio de sesión" /failure:enable`.
