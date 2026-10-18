# Configuración de Auth0 paso a paso

Guía para configurar la identidad de DBA Assistant en Auth0.
Los nombres de los menús pueden variar levemente según la versión del panel de Auth0.

> Los secretos (*Client Secret*) **nunca** se comparten por chat ni se suben al
> repositorio: van solo en el archivo `.env` local.

## Cómo funciona

- Auth0 verifica la **identidad** (Google o email y contraseña) y el **doble factor**.
- Cada empresa cliente es una **Organization** de Auth0. La app la crea sola al dar de
  alta el tenant (`crear-tenant`) e invita al primer Administrador por mail.
- Cada empresa entra por su propia dirección: `https://<cliente>.localhost` en desarrollo.
- El **rol** y los permisos de cada usuario se guardan en la base del tenant, no en Auth0.

## 1. Tenant

Ya creado: `dev-4i7gi6nptyn2o18z` (región US-5, ambiente Development).

El **dominio** del tenant se ve en **Applications → Applications → (cualquier aplicación)
→ Settings → Domain**. Tiene la forma `dev-4i7gi6nptyn2o18z.<región>.auth0.com`.
Ese valor va en `AUTH0_DOMAIN`.

## 2. Aplicación web

1. **Applications → Applications → Create Application**.
2. Nombre: `DBA Assistant`. Tipo: **Regular Web Applications**.
3. Pestaña **Settings**: las URLs permitidas (*Callback*, *Logout* y *Web Origins*) no se
   cargan a mano. `crear-tenant` agrega automáticamente las de cada cliente
   (`https://<cliente>.localhost/...`). Para un tenant ya creado se pueden volver a
   registrar con `python -m app.cli sincronizar-auth0 --slug <cliente>`.
4. Pestaña **Organizations** (dentro de la aplicación):
   - **Types of Users**: *Business Users*.
   - **Login Flow**: *No Prompt* (la empresa se deduce de la dirección).
5. Guardar y anotar **Domain**, **Client ID** y **Client Secret**.

## 3. API (para que el token sea un JWT)

1. **Applications → APIs → Create API**.
2. Nombre: `DBA Assistant API`. Identifier: `https://api.dba-assistant` (es un
   identificador, no necesita existir). Algoritmo: **RS256**.
3. No hace falta activar RBAC: los roles los maneja la app.
4. En la aplicación `DBA Assistant`, pestaña **API Access**, fila **DBA Assistant API** →
   **Edit** → habilitar **User-delegated Access** (sin tildar permisos). Sin esto Auth0
   responde *"Client … is not authorized to access resource server"*.

## 4. Formas de ingreso

1. **Authentication → Social → Google**: habilitar. Para pruebas alcanzan las claves de
   desarrollo de Auth0. Para uso real se crea un cliente OAuth propio en Google Cloud:
   1. <https://console.cloud.google.com> → crear proyecto.
   2. **APIs y servicios → Pantalla de consentimiento OAuth**: tipo *Externo*.
   3. **Credenciales → Crear credenciales → ID de cliente de OAuth** → *Aplicación web*.
   4. **URI de redireccionamiento autorizados**: `https://<dominio-del-tenant>/login/callback`.
   5. Copiar el *Client ID* y el *Client Secret* de Google en la conexión de Auth0.
2. **Authentication → Database → Username-Password-Authentication**: dejarla habilitada
   (ingreso con email y contraseña).
3. En la aplicación `DBA Assistant`, pestaña **Connections**: habilitar **google-oauth2**
   y **Username-Password-Authentication**.

## 5. Doble factor (MFA)

1. **Security → Multi-factor Auth**.
2. Activar **One-time Password** (Google Authenticator, Microsoft Authenticator, etc.).
3. En **Require Multi-factor Auth** elegir **Always**.

## 6. Organizations

1. **Organizations**: verificar que el menú esté disponible en el panel. Si no aparece,
   avisar: depende del plan contratado.
2. No hace falta crear ninguna a mano: las crea `crear-tenant`.

## 7. Aplicación para administración automática (Machine to Machine)

Permite que la app cree las organizaciones e invite usuarios sin intervención manual.

1. **Applications → Applications → Create Application**.
2. Nombre: `DBA Assistant - Administración`. Tipo: **Machine to Machine Applications**.
3. API autorizada: **Auth0 Management API**. Permisos (*scopes*):
   - `read:organizations`, `create:organizations`, `update:organizations`
   - `create:organization_connections`, `read:organization_connections`
   - `create:organization_invitations`, `read:organization_invitations`
   - `read:organization_members`, `delete:organization_members`
   - `read:users`, `update:users`, `delete:users`
   - `read:connections`
   - `read:clients`, `update:clients` (para registrar las URLs de cada cliente)
4. Anotar el **Client ID** y el **Client Secret** de esta aplicación.

## 8. Variables de entorno

Completar en `.env`:

```
AUTH0_DOMAIN=dev-4i7gi6nptyn2o18z.<región>.auth0.com
AUTH0_CLIENT_ID=<client id de "DBA Assistant">
AUTH0_AUDIENCE=https://api.dba-assistant
AUTH0_ADMIN_CLIENT_ID=<client id de "DBA Assistant - Administración">
SECRETO_AUTH0_CLIENT_SECRET=<client secret de "DBA Assistant">
SECRETO_AUTH0_ADMIN_CLIENT_SECRET=<client secret de "DBA Assistant - Administración">
```
