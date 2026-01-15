# Quick Start: Bootstrap Admin User

This guide shows how to create the initial admin user for the Code Factory system.

## Prerequisites

- API server running or ready to start
- Command-line access to the system
- OpenSSL or similar tool for key generation

## Step-by-Step Bootstrap Process

### 1. Generate Bootstrap Key

```bash
# Generate a secure 256-bit key
export BOOTSTRAP_API_KEY=$(openssl rand -hex 32)

# Save it securely (for this session only!)
echo "Bootstrap Key: $BOOTSTRAP_API_KEY" > ~/bootstrap-key-$(date +%Y%m%d).txt
chmod 600 ~/bootstrap-key-$(date +%Y%m%d).txt
```

### 2. Start the API Server (if not running)

```bash
# Start API in background
python -m generator.main.main --interface api &

# Wait for it to be ready
sleep 5
```

### 3. Create Admin User

```bash
# Interactive mode (recommended for first-time setup)
python -m generator.main.cli admin create-user

# You will be prompted for:
# - Admin username: [enter username]
# - Admin password: [enter secure password]
# - Admin password (confirm): [re-enter password]
# - Admin email (optional): [enter email or press Enter]
```

**Password Requirements:**
- Minimum 8 characters
- Recommended: 16+ characters
- Should include:
  - Uppercase letters (A-Z)
  - Lowercase letters (a-z)
  - Numbers (0-9)
  - Special characters (!@#$%^&*)

### 4. Verify Admin User

```bash
# Get a JWT token
curl -X POST http://localhost:8000/api/v1/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=youradmin&password=yourpassword"

# You should receive:
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

### 5. Secure the Bootstrap Key

```bash
# IMPORTANT: Remove or rotate the bootstrap key
unset BOOTSTRAP_API_KEY

# Delete the temporary file
rm ~/bootstrap-key-*.txt

# Or rotate to a new key for emergency use
export BOOTSTRAP_API_KEY=$(openssl rand -hex 32)
echo "New Bootstrap Key: $BOOTSTRAP_API_KEY" >> /secure/vault/bootstrap.key
```

## Non-Interactive Mode

For automated deployments:

```bash
export BOOTSTRAP_API_KEY=$(openssl rand -hex 32)

python -m generator.main.cli admin create-user \
  --username admin \
  --password "$(openssl rand -base64 32)" \
  --email admin@yourcompany.com \
  --scopes admin,user,run,parse,feedback,logs \
  --api-endpoint http://localhost:8000/api/v1/users \
  --timeout 10
```

## Troubleshooting

### Error: "BOOTSTRAP_API_KEY environment variable is not set"

**Solution:**
```bash
export BOOTSTRAP_API_KEY=$(openssl rand -hex 32)
```

### Error: "Password is weak"

**Solution:** Use a stronger password:
```bash
# Generate a strong password
openssl rand -base64 32
```

### Error: "Cannot connect to API server"

**Solution:** Start the API server:
```bash
python -m generator.main.main --interface api
```

### Error: "User already exists"

**Solution:** The admin user is already created. Use the existing credentials or reset via database:
```bash
# Contact your database administrator to reset the user
```

## Security Best Practices

1. **Generate Unique Bootstrap Keys**
   - One key per deployment/environment
   - Never reuse across systems
   - Store in secure vault (HashiCorp Vault, AWS Secrets Manager, etc.)

2. **Use Strong Passwords**
   - Minimum 16 characters for admin accounts
   - Use a password manager
   - Enable MFA when available

3. **Rotate Credentials Regularly**
   - Change admin password every 90 days
   - Rotate bootstrap key after initial setup
   - Audit user access quarterly

4. **Limit Admin Access**
   - Create separate users for day-to-day operations
   - Use admin account only for administrative tasks
   - Monitor admin account usage

5. **Audit Trail**
   - All admin user creation attempts are logged
   - Review logs regularly:
     ```bash
     python -m generator.main.cli logs --query "admin user" --limit 50
     ```

## Next Steps

After creating the admin user:

1. **Test Login**
   ```bash
   curl -X POST http://localhost:8000/api/v1/token \
     -d "username=admin&password=yourpassword"
   ```

2. **Create Regular Users**
   ```bash
   # Use the admin JWT token to create other users
   curl -X POST http://localhost:8000/api/v1/users \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"username":"developer","password":"devpass","scopes":["user","run"]}'
   ```

3. **Configure MFA** (if supported)
   - Follow your organization's MFA policy
   - Use authenticator apps (Google Authenticator, Authy, etc.)

4. **Document Admin Credentials**
   - Store in company password vault
   - Include in disaster recovery plan
   - Share with authorized personnel only

## Emergency Access

If you lose admin credentials:

1. **Option 1: Database Reset**
   ```sql
   -- Direct database access (PostgreSQL example)
   UPDATE users SET hashed_password = '$2b$...' WHERE username = 'admin';
   ```

2. **Option 2: Create New Admin with Bootstrap**
   ```bash
   # Use bootstrap key to create a new admin user
   export BOOTSTRAP_API_KEY=your-emergency-bootstrap-key
   python -m generator.main.cli admin create-user --username recovery-admin
   ```

3. **Option 3: Contact System Administrator**
   - Follow your organization's account recovery process

## Additional Resources

- Full documentation: [MAIN_ENTRY_FIXES.md](./MAIN_ENTRY_FIXES.md)
- Security guidelines: [SECURITY_DEPLOYMENT_GUIDE.md](./SECURITY_DEPLOYMENT_GUIDE.md)
- API documentation: [http://localhost:8000/api/v1/docs](http://localhost:8000/api/v1/docs)

## Support

For issues or questions:
- Check logs: `python -m generator.main.cli logs --query error`
- Run health check: `python -m generator.main.cli health`
- Contact: support@yourcompany.com

---

**Security Notice:** This bootstrap process should only be used during initial deployment. After the first admin user is created, use the admin account to manage all other users through the API.
