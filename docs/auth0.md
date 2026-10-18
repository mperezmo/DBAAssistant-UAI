# Configuración de Auth0 paso a paso

Guía para crear desde cero la identidad de DBA Assistant en Auth0.
Los nombres de menús pueden variar levemente según la versión del panel de Auth0.

> Los secretos (Client Secret) **nunca** se comparten por chat ni se suben al
> repositorio: van solo en el archivo `.env` local.

## 1. Cuenta y tenant

1. Crear una cuenta en <https://auth0.com> (el plan gratuito alcanza para desarrollo).
2. Al registrarse se crea un **tenant**. Sugerido: nombre `dba-assistant-dev`, región **US** o **EU**.
3. Anotar el dominio del tenant, por ejemplo `dba-assistant-dev.us.auth0.com`.

## 2. Aplicación

1. **Applications → Applications → Create Application**.
2. Nombre: `DBA Assistant`. Tipo: **Regular Web Applications** (la app se renderiza en el servidor).
3. En la pestaña **Settings** completar:
   - **Allowed Callback URLs**: `https://localhost/auth/callback`
   - **Allowed Logout URLs**: `https://localhost/`
   - **Allowed Web Origins**: `https://localhost`
4. Guardar y anotar **Domain**, **Client ID** y **Client Secret**.

## 3. API (para los permisos)

1. **Applications → APIs → Create API**.
2. Nombre: `DBA Assistant API`. Identifier: `https://api.dba-assistant` (es un identificador, no necesita existir). Algoritmo: **RS256**.
3. En **Settings → RBAC Settings** activar:
   - **Enable RBAC**
   - **Add Permissions in the Access Token**
4. En la pestaña **Permissions** se cargan los permisos de cada módulo. La lista definitiva se arma junto con los casos de uso.

## 4. Login con Google

1. **Authentication → Social → Google** (o *Create Connection → Google*).
2. Para pruebas, Auth0 permite usar sus claves de desarrollo. Para uso real se necesita un cliente OAuth propio:
   1. En <https://console.cloud.google.com> crear un proyecto.
   2. **APIs y servicios → Pantalla de consentimiento OAuth**: tipo *Externo*, completar nombre y correo.
   3. **Credenciales → Crear credenciales → ID de cliente de OAuth** → tipo *Aplicación web*.
   4. En **URI de redireccionamiento autorizados** poner `https://<dominio-del-tenant>/login/callback`.
   5. Copiar el *Client ID* y *Client Secret* de Google en la conexión de Auth0.
3. En la aplicación `DBA Assistant`, pestaña **Connections**: dejar habilitado **solo google-oauth2** y deshabilitar *Username-Password-Authentication*.

## 5. Doble factor (MFA)

1. **Security → Multi-factor Auth**.
2. Activar **One-time Password** (app de códigos: Google Authenticator, Microsoft Authenticator, etc.).
3. En **Require Multi-factor Auth** elegir **Always**: se pide el segundo factor en cada ingreso.
4. El segundo factor adicional para ejecutar acciones sobre conexiones **PROD** lo resuelve la aplicación pidiendo una reautenticación en ese momento; no requiere configuración extra en el panel.

## 6. Roles

1. **User Management → Roles → Create Role**, uno por cada rol definido (ver `docs/pendientes.md`).
2. En cada rol, pestaña **Permissions → Add Permissions**, elegir la API `DBA Assistant API` y asignar los permisos correspondientes.

## 7. Roles dentro del token

Para que la aplicación conozca los roles del usuario:

1. **Actions → Library → Create Action → Build from scratch**.
2. Nombre: `Agregar roles al token`. Trigger: **Login / Post Login**.
3. Código:

   ```js
   exports.onExecutePostLogin = async (event, api) => {
     const namespace = "https://dba-assistant";
     const roles = event.authorization?.roles ?? [];
     api.idToken.setCustomClaim(`${namespace}/roles`, roles);
     api.accessToken.setCustomClaim(`${namespace}/roles`, roles);
   };
   ```

4. **Deploy**. Luego, en **Actions → Triggers → post-login**, arrastrar la acción al flujo y **Apply**.

## 8. Primer usuario

1. Ingresar una vez a la aplicación con la cuenta de Google (cuando el login esté implementado).
2. En **User Management → Users** buscar el usuario y, en la pestaña **Roles**, asignarle el rol de administrador.

## 9. Variables de entorno

Completar en `.env`:

```
AUTH0_DOMAIN=dba-assistant-dev.us.auth0.com
AUTH0_CLIENT_ID=<client id>
AUTH0_AUDIENCE=https://api.dba-assistant
SECRETO_AUTH0_CLIENT_SECRET=<client secret>
```
