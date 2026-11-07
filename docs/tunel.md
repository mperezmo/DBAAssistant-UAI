# Túnel público para probar desde el celular

Las notificaciones push y las passkeys (biometría) necesitan que la app se sirva por HTTPS
con un certificado válido. En desarrollo la app corre en `https://<empresa>.localhost`
con un certificado autofirmado, que el celular no acepta. Un túnel de **Cloudflare** expone
el NGINX local en un dominio propio con certificado válido, sin abrir puertos en el router.

Es un desvío temporal del diagrama hasta el despliegue en Azure (ver `docs/pendientes.md`).

## Qué hace falta

- Un dominio propio administrado en Cloudflare (plan gratuito), por ejemplo `midominio.com`.
- Elegir el dominio base de la app. Cada empresa entra por un subdominio de primer nivel,
  así que lo más simple es usar el dominio raíz: `https://demo.midominio.com`.
  (El certificado gratuito de Cloudflare cubre `*.midominio.com`, no `*.dba.midominio.com`.)

## 1. Crear el túnel

1. Cloudflare → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel**.
2. Tipo **Cloudflared**, nombre `dba-assistant`.
3. En "Install and run a connector" copiar el **token** (la cadena larga después de
   `--token`). No usar el comando de instalación: el conector corre en Docker Compose.
4. Pegar el token en el `.env`:
   ```
   SECRETO_CLOUDFLARE_TUNNEL_TOKEN=<token>
   ```

## 2. Publicar las direcciones

En el túnel → **Public Hostname** → **Add a public hostname**:

| Campo | Valor |
|---|---|
| Subdomain | `*` |
| Domain | `midominio.com` |
| Service | `HTTPS` · `nginx:443` |
| Additional settings → TLS → **No TLS Verify** | activado (NGINX usa un certificado autofirmado dentro de la red de Docker) |

Si el panel no crea solo el registro DNS comodín, agregarlo en **DNS**: tipo `CNAME`,
nombre `*`, destino `<id-del-túnel>.cfargotunnel.com`, con el proxy activado (nube naranja).

## 3. Configurar la app

1. En el `.env`: `DOMINIO_BASE=midominio.com`.
2. Levantar todo con el túnel:
   ```bash
   docker compose --profile tunel up -d --build
   ```
3. Registrar en Auth0 las direcciones nuevas de cada empresa:
   ```bash
   docker compose exec backend python -m app.cli sincronizar-auth0 --slug demo
   ```
4. Entrar desde el celular a `https://demo.midominio.com`.

## 4. Probar push y biometría

- **Push:** en *Alertas*, activar las notificaciones en el celular (en iPhone, primero
  "Agregar a pantalla de inicio" y abrir la app instalada). Se ven en *Mi perfil →
  Dispositivos con notificaciones*.
- **Biometría:** en *Mi perfil → Biometría*, registrar el dispositivo (pide el segundo
  factor de Auth0 una vez). Desde ahí, las acciones sobre PROD se confirman con la huella
  o el rostro sin salir de la app.

Las passkeys quedan atadas a la dirección donde se registraron: las registradas en
`demo.localhost` no sirven en `demo.midominio.com` y viceversa.

## Apagar el túnel

```bash
docker compose stop tunel
```
Con `docker compose up -d` (sin `--profile tunel`) el túnel no arranca.
