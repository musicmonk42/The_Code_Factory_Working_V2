"""
Secrets Management Module

This module provides a unified interface for managing secrets across different providers:
- AWS Secrets Manager
- HashiCorp Vault
- Google Cloud Secret Manager
- Azure Key Vault
- Environment variables (for development)

Usage:
    from omnicore_engine.secrets_manager import get_secret, SecretProvider
    
    # Get secret from configured provider
    api_key = get_secret("OPENAI_API_KEY")
    
    # Get secret from specific provider
    db_password = get_secret("DATABASE_PASSWORD", provider=SecretProvider.AWS)
"""

import os
import json
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class SecretProvider(Enum):
    """Supported secret providers"""
    ENV = "env"
    AWS = "aws"
    VAULT = "vault"
    GCP = "gcp"
    AZURE = "azure"


class SecretManagerBase(ABC):
    """Base class for secret managers"""
    
    @abstractmethod
    def get_secret(self, secret_name: str) -> Optional[str]:
        """Retrieve a secret value"""
        pass
    
    @abstractmethod
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """Store a secret value"""
        pass
    
    @abstractmethod
    def delete_secret(self, secret_name: str) -> bool:
        """Delete a secret"""
        pass
    
    @abstractmethod
    def list_secrets(self) -> list:
        """List available secrets"""
        pass


class EnvSecretManager(SecretManagerBase):
    """Environment variable based secret manager (for development)"""
    
    def get_secret(self, secret_name: str) -> Optional[str]:
        """Get secret from environment variable"""
        return os.environ.get(secret_name)
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """Set environment variable"""
        os.environ[secret_name] = secret_value
        return True
    
    def delete_secret(self, secret_name: str) -> bool:
        """Delete environment variable"""
        if secret_name in os.environ:
            del os.environ[secret_name]
            return True
        return False
    
    def list_secrets(self) -> list:
        """List environment variables"""
        return list(os.environ.keys())


class AWSSecretsManager(SecretManagerBase):
    """AWS Secrets Manager integration"""
    
    def __init__(self, region_name: Optional[str] = None):
        """
        Initialize AWS Secrets Manager client
        
        Args:
            region_name: AWS region (defaults to AWS_REGION env var)
        """
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self._client = None
    
    @property
    def client(self):
        """Lazy load boto3 client"""
        if self._client is None:
            try:
                import boto3
                self._client = boto3.client(
                    'secretsmanager',
                    region_name=self.region_name
                )
            except ImportError:
                logger.error("boto3 is required for AWS Secrets Manager. Install with: pip install boto3")
                raise
        return self._client
    
    def get_secret(self, secret_name: str) -> Optional[str]:
        """
        Retrieve secret from AWS Secrets Manager
        
        Args:
            secret_name: Name of the secret
            
        Returns:
            Secret value or None if not found
        """
        try:
            response = self.client.get_secret_value(SecretId=secret_name)
            
            # Secrets can be string or binary
            if 'SecretString' in response:
                return response['SecretString']
            else:
                import base64
                return base64.b64decode(response['SecretBinary']).decode('utf-8')
                
        except self.client.exceptions.ResourceNotFoundException:
            logger.warning(f"Secret '{secret_name}' not found in AWS Secrets Manager")
            return None
        except Exception as e:
            logger.error(f"Error retrieving secret '{secret_name}': {e}")
            return None
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """
        Store secret in AWS Secrets Manager
        
        Args:
            secret_name: Name of the secret
            secret_value: Value to store
            
        Returns:
            True if successful, False otherwise
        """
        try:
            try:
                # Try to update existing secret
                self.client.put_secret_value(
                    SecretId=secret_name,
                    SecretString=secret_value
                )
            except self.client.exceptions.ResourceNotFoundException:
                # Create new secret if it doesn't exist
                self.client.create_secret(
                    Name=secret_name,
                    SecretString=secret_value
                )
            return True
        except Exception as e:
            logger.error(f"Error storing secret '{secret_name}': {e}")
            return False
    
    def delete_secret(self, secret_name: str) -> bool:
        """
        Delete secret from AWS Secrets Manager
        
        Args:
            secret_name: Name of the secret
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=False  # Allow recovery within 30 days
            )
            return True
        except Exception as e:
            logger.error(f"Error deleting secret '{secret_name}': {e}")
            return False
    
    def list_secrets(self) -> list:
        """
        List all secrets in AWS Secrets Manager
        
        Returns:
            List of secret names
        """
        try:
            response = self.client.list_secrets()
            return [secret['Name'] for secret in response.get('SecretList', [])]
        except Exception as e:
            logger.error(f"Error listing secrets: {e}")
            return []


class VaultSecretManager(SecretManagerBase):
    """HashiCorp Vault integration"""
    
    def __init__(self, url: Optional[str] = None, token: Optional[str] = None, mount_point: str = "secret"):
        """
        Initialize Vault client
        
        Args:
            url: Vault server URL (defaults to VAULT_ADDR env var)
            token: Vault token (defaults to VAULT_TOKEN env var)
            mount_point: KV secrets engine mount point
        """
        self.url = url or os.environ.get("VAULT_ADDR", "http://localhost:8200")
        self.token = token or os.environ.get("VAULT_TOKEN")
        self.mount_point = mount_point
        self._client = None
    
    @property
    def client(self):
        """Lazy load hvac client"""
        if self._client is None:
            try:
                import hvac
                self._client = hvac.Client(url=self.url, token=self.token)
                if not self._client.is_authenticated():
                    logger.error("Vault authentication failed")
                    raise Exception("Vault authentication failed")
            except ImportError:
                logger.error("hvac is required for Vault. Install with: pip install hvac")
                raise
        return self._client
    
    def get_secret(self, secret_name: str) -> Optional[str]:
        """
        Retrieve secret from Vault
        
        Args:
            secret_name: Path to the secret (e.g., 'app/config/api_key')
            
        Returns:
            Secret value or None if not found
        """
        try:
            # KV v2 secrets engine
            response = self.client.secrets.kv.v2.read_secret_version(
                path=secret_name,
                mount_point=self.mount_point
            )
            
            # Return the data field
            data = response.get('data', {}).get('data', {})
            
            # If the secret has a single 'value' field, return it
            if 'value' in data:
                return data['value']
            
            # Otherwise return the entire data as JSON
            return json.dumps(data)
            
        except Exception as e:
            logger.warning(f"Error retrieving secret '{secret_name}' from Vault: {e}")
            return None
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """
        Store secret in Vault
        
        Args:
            secret_name: Path to the secret
            secret_value: Value to store
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.secrets.kv.v2.create_or_update_secret(
                path=secret_name,
                secret={'value': secret_value},
                mount_point=self.mount_point
            )
            return True
        except Exception as e:
            logger.error(f"Error storing secret '{secret_name}' in Vault: {e}")
            return False
    
    def delete_secret(self, secret_name: str) -> bool:
        """
        Delete secret from Vault
        
        Args:
            secret_name: Path to the secret
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=secret_name,
                mount_point=self.mount_point
            )
            return True
        except Exception as e:
            logger.error(f"Error deleting secret '{secret_name}' from Vault: {e}")
            return False
    
    def list_secrets(self) -> list:
        """
        List all secrets in Vault
        
        Returns:
            List of secret paths
        """
        try:
            response = self.client.secrets.kv.v2.list_secrets(
                path='',
                mount_point=self.mount_point
            )
            return response.get('data', {}).get('keys', [])
        except Exception as e:
            logger.error(f"Error listing secrets from Vault: {e}")
            return []


class GCPSecretManager(SecretManagerBase):
    """Google Cloud Secret Manager integration"""
    
    def __init__(self, project_id: Optional[str] = None):
        """
        Initialize GCP Secret Manager client
        
        Args:
            project_id: GCP project ID (defaults to GCP_PROJECT_ID env var)
        """
        self.project_id = project_id or os.environ.get("GCP_PROJECT_ID")
        if not self.project_id:
            raise ValueError("GCP_PROJECT_ID must be set")
        self._client = None
    
    @property
    def client(self):
        """Lazy load GCP client"""
        if self._client is None:
            try:
                from google.cloud import secretmanager
                self._client = secretmanager.SecretManagerServiceClient()
            except ImportError:
                logger.error("google-cloud-secret-manager is required. Install with: pip install google-cloud-secret-manager")
                raise
        return self._client
    
    def get_secret(self, secret_name: str) -> Optional[str]:
        """
        Retrieve secret from GCP Secret Manager
        
        Args:
            secret_name: Name of the secret
            
        Returns:
            Secret value or None if not found
        """
        try:
            name = f"projects/{self.project_id}/secrets/{secret_name}/versions/latest"
            response = self.client.access_secret_version(request={"name": name})
            return response.payload.data.decode('UTF-8')
        except Exception as e:
            logger.warning(f"Error retrieving secret '{secret_name}' from GCP: {e}")
            return None
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """
        Store secret in GCP Secret Manager
        
        Args:
            secret_name: Name of the secret
            secret_value: Value to store
            
        Returns:
            True if successful, False otherwise
        """
        try:
            parent = f"projects/{self.project_id}"
            
            try:
                # Try to create the secret
                self.client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": secret_name,
                        "secret": {"replication": {"automatic": {}}}
                    }
                )
            except Exception:
                # Secret already exists, that's ok
                pass
            
            # Add secret version
            secret_path = f"{parent}/secrets/{secret_name}"
            self.client.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": secret_value.encode('UTF-8')}
                }
            )
            return True
        except Exception as e:
            logger.error(f"Error storing secret '{secret_name}' in GCP: {e}")
            return False
    
    def delete_secret(self, secret_name: str) -> bool:
        """
        Delete secret from GCP Secret Manager
        
        Args:
            secret_name: Name of the secret
            
        Returns:
            True if successful, False otherwise
        """
        try:
            name = f"projects/{self.project_id}/secrets/{secret_name}"
            self.client.delete_secret(request={"name": name})
            return True
        except Exception as e:
            logger.error(f"Error deleting secret '{secret_name}' from GCP: {e}")
            return False
    
    def list_secrets(self) -> list:
        """
        List all secrets in GCP Secret Manager
        
        Returns:
            List of secret names
        """
        try:
            parent = f"projects/{self.project_id}"
            response = self.client.list_secrets(request={"parent": parent})
            return [secret.name.split('/')[-1] for secret in response]
        except Exception as e:
            logger.error(f"Error listing secrets from GCP: {e}")
            return []


class AzureKeyVaultManager(SecretManagerBase):
    """Azure Key Vault integration"""
    
    def __init__(self, vault_url: Optional[str] = None):
        """
        Initialize Azure Key Vault client
        
        Args:
            vault_url: Key Vault URL (defaults to AZURE_KEY_VAULT_URL env var)
        """
        self.vault_url = vault_url or os.environ.get("AZURE_KEY_VAULT_URL")
        if not self.vault_url:
            raise ValueError("AZURE_KEY_VAULT_URL must be set")
        self._client = None
    
    @property
    def client(self):
        """Lazy load Azure client"""
        if self._client is None:
            try:
                from azure.keyvault.secrets import SecretClient
                from azure.identity import DefaultAzureCredential
                
                credential = DefaultAzureCredential()
                self._client = SecretClient(vault_url=self.vault_url, credential=credential)
            except ImportError:
                logger.error("azure-keyvault-secrets and azure-identity are required. Install with: pip install azure-keyvault-secrets azure-identity")
                raise
        return self._client
    
    def get_secret(self, secret_name: str) -> Optional[str]:
        """
        Retrieve secret from Azure Key Vault
        
        Args:
            secret_name: Name of the secret
            
        Returns:
            Secret value or None if not found
        """
        try:
            secret = self.client.get_secret(secret_name)
            return secret.value
        except Exception as e:
            logger.warning(f"Error retrieving secret '{secret_name}' from Azure: {e}")
            return None
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """
        Store secret in Azure Key Vault
        
        Args:
            secret_name: Name of the secret
            secret_value: Value to store
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.set_secret(secret_name, secret_value)
            return True
        except Exception as e:
            logger.error(f"Error storing secret '{secret_name}' in Azure: {e}")
            return False
    
    def delete_secret(self, secret_name: str) -> bool:
        """
        Delete secret from Azure Key Vault
        
        Args:
            secret_name: Name of the secret
            
        Returns:
            True if successful, False otherwise
        """
        try:
            poller = self.client.begin_delete_secret(secret_name)
            poller.wait()
            return True
        except Exception as e:
            logger.error(f"Error deleting secret '{secret_name}' from Azure: {e}")
            return False
    
    def list_secrets(self) -> list:
        """
        List all secrets in Azure Key Vault
        
        Returns:
            List of secret names
        """
        try:
            return [secret.name for secret in self.client.list_properties_of_secrets()]
        except Exception as e:
            logger.error(f"Error listing secrets from Azure: {e}")
            return []


# Singleton instance
_secret_manager: Optional[SecretManagerBase] = None


def get_secret_manager(provider: Optional[SecretProvider] = None) -> SecretManagerBase:
    """
    Get or create a secret manager instance
    
    Args:
        provider: Secret provider to use (defaults to SECRETS_PROVIDER env var)
        
    Returns:
        SecretManagerBase instance
    """
    global _secret_manager
    
    if provider is None:
        provider_str = os.environ.get("SECRETS_PROVIDER", "env").lower()
        try:
            provider = SecretProvider(provider_str)
        except ValueError:
            logger.warning(f"Unknown provider '{provider_str}', using ENV")
            provider = SecretProvider.ENV
    
    # Use singleton for default provider
    if _secret_manager is None and provider is None:
        if provider == SecretProvider.ENV:
            _secret_manager = EnvSecretManager()
        elif provider == SecretProvider.AWS:
            _secret_manager = AWSSecretsManager()
        elif provider == SecretProvider.VAULT:
            _secret_manager = VaultSecretManager()
        elif provider == SecretProvider.GCP:
            _secret_manager = GCPSecretManager()
        elif provider == SecretProvider.AZURE:
            _secret_manager = AzureKeyVaultManager()
    
    if _secret_manager and provider is None:
        return _secret_manager
    
    # Create appropriate manager for explicit provider
    if provider == SecretProvider.ENV:
        return EnvSecretManager()
    elif provider == SecretProvider.AWS:
        return AWSSecretsManager()
    elif provider == SecretProvider.VAULT:
        return VaultSecretManager()
    elif provider == SecretProvider.GCP:
        return GCPSecretManager()
    elif provider == SecretProvider.AZURE:
        return AzureKeyVaultManager()
    else:
        raise ValueError(f"Unsupported provider: {provider}")


def get_secret(secret_name: str, provider: Optional[SecretProvider] = None, default: Optional[str] = None) -> Optional[str]:
    """
    Retrieve a secret from the configured provider
    
    Args:
        secret_name: Name of the secret
        provider: Optional provider override
        default: Default value if secret not found
        
    Returns:
        Secret value or default
        
    Example:
        api_key = get_secret("OPENAI_API_KEY")
        db_password = get_secret("DATABASE_PASSWORD", provider=SecretProvider.AWS)
    """
    manager = get_secret_manager(provider)
    value = manager.get_secret(secret_name)
    return value if value is not None else default


def set_secret(secret_name: str, secret_value: str, provider: Optional[SecretProvider] = None) -> bool:
    """
    Store a secret in the configured provider
    
    Args:
        secret_name: Name of the secret
        secret_value: Value to store
        provider: Optional provider override
        
    Returns:
        True if successful, False otherwise
    """
    manager = get_secret_manager(provider)
    return manager.set_secret(secret_name, secret_value)


def delete_secret(secret_name: str, provider: Optional[SecretProvider] = None) -> bool:
    """
    Delete a secret from the configured provider
    
    Args:
        secret_name: Name of the secret
        provider: Optional provider override
        
    Returns:
        True if successful, False otherwise
    """
    manager = get_secret_manager(provider)
    return manager.delete_secret(secret_name)


def list_secrets(provider: Optional[SecretProvider] = None) -> list:
    """
    List all secrets in the configured provider
    
    Args:
        provider: Optional provider override
        
    Returns:
        List of secret names
    """
    manager = get_secret_manager(provider)
    return manager.list_secrets()
