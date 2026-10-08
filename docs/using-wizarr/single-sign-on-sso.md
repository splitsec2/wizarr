# Single-Sign-On (SSO)

#### **Wizarr supports SSO via disabling its inbuilt authentication**

To Disable Wizarr's inbuilt authentication in order to put it behind a Proxy Provider (Authelia, Authentik...), set the following variable:

`DISABLE_BUILTIN_AUTH=True`

#### Whitelist Public Paths (important!)

In order to make the invitation process available for non signed in users, make sure you whitelist the following paths:

{% tabs %}
{% tab title="Authelia" %}
```
    - domain: wizarr.domain.com
      resources:
        - '^/join(/.*)?$'
        - '^/j(/.*)?$'
        - '^/static(/.*)?$'
        - '^/setup(/.*)?$'
        - '^/wizard(/.*)?$'
        - '^/image-proxy(/.*)?$'
        - '^/cinema-posters(/.*)?$'
    
      policy: bypass
```
{% endtab %}

{% tab title="Authentik/Other" %}
```
- '^/join($|/.*)'
- '^/j($|/.*)'
- '^/static($|/.*)'
- '^/setup($|/.*)'
- '^/wizard($|/.*)'
- '^/image-proxy($|/.*)'
- '^/cinema-posters($|/.*)'
```
{% endtab %}
{% endtabs %}

#### Cloudflare Access: verify the token

`DISABLE_BUILTIN_AUTH=True` trusts whatever reaches Wizarr. Anything that can reach its port directly, such as another container on the same host or a misconfigured firewall, gets the admin pages without going through your proxy.

If Wizarr sits behind Cloudflare Access, it can check Access's signed token itself instead. Set both of these:

```
CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com
CF_ACCESS_AUD=<the application's AUD tag>
```

The AUD tag is in Zero Trust under Access > Applications > your Wizarr app > Additional settings > Application Audience (AUD) Tag.

With both set, every admin request must carry a valid `Cf-Access-Jwt-Assertion` header: signed by your team's key, for this application, issued by your team domain and not expired. A session cookie on its own grants nothing. This takes precedence over `DISABLE_BUILTIN_AUTH` and the built-in login. The invitation paths above stay public, `/health` needs no token, and API keys work as before.

If the signing keys can't be fetched from `https://<team>/cdn-cgi/access/certs`, admin access is refused rather than allowed. To get back in, unset the two variables and restart.

Each admin signs in as their own Wizarr admin account, matched by the email in the Access token. Before you set the two variables, open Settings > Admins, edit your account and fill in **Cloudflare Access email** with the address you sign in to Access with, or you will lock yourself out. With Access on, **Add Admin** asks for that email and a name instead of a username and password. An admin also has to be allowed by your Access policy: the policy decides who reaches Wizarr, the admin account decides who is an admin. Someone Access lets through without an account sees "isn't a Wizarr admin yet". Service tokens carry no email, so they can't sign in as an admin. Invites record the admin who created them, and an invite made with an API key records the admin who created the key.
