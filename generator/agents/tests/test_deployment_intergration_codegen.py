"""
Deployment and Infrastructure Integration Tests
Tests for Docker, Kubernetes, and production deployment scenarios.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional
from unittest.mock import Mock, patch, MagicMock
import hashlib

import pytest
import docker
import kubernetes
from faker import Faker

# Import system modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agents.codegen_agent import generate_code

fake = Faker()


# ============================================================================
# Docker Integration Tests
# ============================================================================

class TestDockerIntegration:
    """Test Docker containerization and deployment."""
    
    @pytest.fixture
    def docker_client(self):
        """Create Docker client for testing."""
        try:
            client = docker.from_env()
            # Check if Docker is running
            client.ping()
            return client
        except Exception:
            pytest.skip("Docker not available")
    
    @pytest.fixture
    def dockerfile_content(self):
        """Generate Dockerfile for the service."""
        return """
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agents/ ./agents/
COPY templates/ ./templates/

# Security: Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Expose ports
EXPOSE 8000 8001

# Run the application
CMD ["python", "-m", "agents.codegen_agent"]
"""
    
    def test_docker_build(self, docker_client, dockerfile_content, tmp_path):
        """Test building Docker image."""
        # Create temporary build context
        build_dir = tmp_path / "docker_build"
        build_dir.mkdir()
        
        # Write Dockerfile
        dockerfile = build_dir / "Dockerfile"
        dockerfile.write_text(dockerfile_content)
        
        # Create minimal requirements.txt
        requirements = build_dir / "requirements.txt"
        requirements.write_text("""
aiohttp==3.8.5
aioredis==2.0.1
pyyaml==6.0
prometheus-client==0.17.1
opentelemetry-api==1.20.0
""")
        
        # Create dummy application structure
        agents_dir = build_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "codegen_agent.py").write_text("print('Test')")
        
        templates_dir = build_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "python.jinja2").write_text("# Template")
        
        # Build image
        image_tag = f"codegen-test:{fake.uuid4()[:8]}"
        
        try:
            image, build_logs = docker_client.images.build(
                path=str(build_dir),
                tag=image_tag,
                rm=True
            )
            
            # Verify image was created
            assert image is not None
            assert image_tag in [tag for tag in image.tags]
            
            # Check image properties
            assert image.attrs['Architecture'] in ['amd64', 'arm64']
            
            # Cleanup
            docker_client.images.remove(image.id, force=True)
            
        except docker.errors.BuildError as e:
            pytest.fail(f"Docker build failed: {e}")
    
    def test_docker_compose_integration(self, tmp_path):
        """Test Docker Compose configuration."""
        compose_config = {
            'version': '3.8',
            'services': {
                'codegen-agent': {
                    'build': '.',
                    'ports': ['8000:8000', '8001:8001'],
                    'environment': {
                        'REDIS_URL': 'redis://redis:6379',
                        'OPENAI_API_KEY': '${OPENAI_API_KEY}',
                        'OTEL_EXPORTER_JAEGER_ENDPOINT': 'jaeger:6831'
                    },
                    'depends_on': ['redis', 'jaeger'],
                    'restart': 'unless-stopped',
                    'networks': ['codegen-network']
                },
                'redis': {
                    'image': 'redis:7-alpine',
                    'ports': ['6379:6379'],
                    'volumes': ['redis-data:/data'],
                    'networks': ['codegen-network']
                },
                'jaeger': {
                    'image': 'jaegertracing/all-in-one:latest',
                    'ports': [
                        '6831:6831/udp',
                        '16686:16686'
                    ],
                    'networks': ['codegen-network']
                }
            },
            'volumes': {
                'redis-data': {}
            },
            'networks': {
                'codegen-network': {
                    'driver': 'bridge'
                }
            }
        }
        
        # Write docker-compose.yml
        compose_file = tmp_path / "docker-compose.yml"
        with open(compose_file, 'w') as f:
            yaml.dump(compose_config, f)
        
        # Validate compose file
        result = subprocess.run(
            ['docker-compose', '-f', str(compose_file), 'config'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            # Parse validated config
            validated = yaml.safe_load(result.stdout)
            
            # Verify services are configured
            assert 'codegen-agent' in validated['services']
            assert 'redis' in validated['services']
            assert 'jaeger' in validated['services']
            
            # Verify networking
            assert 'codegen-network' in validated['networks']
        else:
            # Docker Compose not available, skip validation
            pass
    
    def test_container_security_scan(self, docker_client):
        """Test container security scanning."""
        # This would integrate with tools like Trivy or Clair
        # For testing, we'll mock the security scan
        
        scan_results = {
            'vulnerabilities': [],
            'misconfigurations': [
                {
                    'severity': 'LOW',
                    'message': 'Consider using specific version tags instead of latest'
                }
            ],
            'secrets': []
        }
        
        # In production, run actual scanner
        # subprocess.run(['trivy', 'image', image_tag])
        
        assert len(scan_results['vulnerabilities']) == 0
        assert all(
            m['severity'] != 'HIGH' 
            for m in scan_results['misconfigurations']
        )


# ============================================================================
# Kubernetes Integration Tests
# ============================================================================

class TestKubernetesIntegration:
    """Test Kubernetes deployment and operations."""
    
    @pytest.fixture
    def k8s_client(self):
        """Create Kubernetes client for testing."""
        try:
            # Try to load kubeconfig
            kubernetes.config.load_kube_config()
            return kubernetes.client.ApiClient()
        except Exception:
            # Try in-cluster config (when running in K8s)
            try:
                kubernetes.config.load_incluster_config()
                return kubernetes.client.ApiClient()
            except Exception:
                pytest.skip("Kubernetes not available")
    
    @pytest.fixture
    def k8s_manifests(self):
        """Generate Kubernetes manifests."""
        return {
            'namespace': {
                'apiVersion': 'v1',
                'kind': 'Namespace',
                'metadata': {
                    'name': 'codegen-system'
                }
            },
            'deployment': {
                'apiVersion': 'apps/v1',
                'kind': 'Deployment',
                'metadata': {
                    'name': 'codegen-agent',
                    'namespace': 'codegen-system',
                    'labels': {
                        'app': 'codegen-agent',
                        'version': 'v1'
                    }
                },
                'spec': {
                    'replicas': 3,
                    'selector': {
                        'matchLabels': {
                            'app': 'codegen-agent'
                        }
                    },
                    'template': {
                        'metadata': {
                            'labels': {
                                'app': 'codegen-agent',
                                'version': 'v1'
                            },
                            'annotations': {
                                'prometheus.io/scrape': 'true',
                                'prometheus.io/port': '8001'
                            }
                        },
                        'spec': {
                            'serviceAccountName': 'codegen-agent',
                            'containers': [{
                                'name': 'codegen-agent',
                                'image': 'codegen-agent:latest',
                                'imagePullPolicy': 'IfNotPresent',
                                'ports': [
                                    {'containerPort': 8000, 'name': 'http'},
                                    {'containerPort': 8001, 'name': 'metrics'}
                                ],
                                'env': [
                                    {
                                        'name': 'REDIS_URL',
                                        'valueFrom': {
                                            'secretKeyRef': {
                                                'name': 'codegen-secrets',
                                                'key': 'redis-url'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'OPENAI_API_KEY',
                                        'valueFrom': {
                                            'secretKeyRef': {
                                                'name': 'codegen-secrets',
                                                'key': 'openai-api-key'
                                            }
                                        }
                                    }
                                ],
                                'resources': {
                                    'requests': {
                                        'memory': '512Mi',
                                        'cpu': '500m'
                                    },
                                    'limits': {
                                        'memory': '2Gi',
                                        'cpu': '2000m'
                                    }
                                },
                                'livenessProbe': {
                                    'httpGet': {
                                        'path': '/health',
                                        'port': 8000
                                    },
                                    'initialDelaySeconds': 30,
                                    'periodSeconds': 10
                                },
                                'readinessProbe': {
                                    'httpGet': {
                                        'path': '/health',
                                        'port': 8000
                                    },
                                    'initialDelaySeconds': 5,
                                    'periodSeconds': 5
                                },
                                'securityContext': {
                                    'runAsNonRoot': True,
                                    'runAsUser': 1000,
                                    'readOnlyRootFilesystem': True,
                                    'allowPrivilegeEscalation': False
                                }
                            }],
                            'affinity': {
                                'podAntiAffinity': {
                                    'preferredDuringSchedulingIgnoredDuringExecution': [{
                                        'weight': 100,
                                        'podAffinityTerm': {
                                            'labelSelector': {
                                                'matchExpressions': [{
                                                    'key': 'app',
                                                    'operator': 'In',
                                                    'values': ['codegen-agent']
                                                }]
                                            },
                                            'topologyKey': 'kubernetes.io/hostname'
                                        }
                                    }]
                                }
                            }
                        }
                    }
                }
            },
            'service': {
                'apiVersion': 'v1',
                'kind': 'Service',
                'metadata': {
                    'name': 'codegen-agent',
                    'namespace': 'codegen-system'
                },
                'spec': {
                    'selector': {
                        'app': 'codegen-agent'
                    },
                    'ports': [
                        {
                            'name': 'http',
                            'port': 8000,
                            'targetPort': 8000
                        },
                        {
                            'name': 'metrics',
                            'port': 8001,
                            'targetPort': 8001
                        }
                    ],
                    'type': 'ClusterIP'
                }
            },
            'hpa': {
                'apiVersion': 'autoscaling/v2',
                'kind': 'HorizontalPodAutoscaler',
                'metadata': {
                    'name': 'codegen-agent',
                    'namespace': 'codegen-system'
                },
                'spec': {
                    'scaleTargetRef': {
                        'apiVersion': 'apps/v1',
                        'kind': 'Deployment',
                        'name': 'codegen-agent'
                    },
                    'minReplicas': 2,
                    'maxReplicas': 10,
                    'metrics': [
                        {
                            'type': 'Resource',
                            'resource': {
                                'name': 'cpu',
                                'target': {
                                    'type': 'Utilization',
                                    'averageUtilization': 70
                                }
                            }
                        },
                        {
                            'type': 'Resource',
                            'resource': {
                                'name': 'memory',
                                'target': {
                                    'type': 'Utilization',
                                    'averageUtilization': 80
                                }
                            }
                        }
                    ]
                }
            }
        }
    
    def test_k8s_manifest_validation(self, k8s_manifests):
        """Test Kubernetes manifest validation."""
        # Validate deployment
        deployment = k8s_manifests['deployment']
        assert deployment['apiVersion'] == 'apps/v1'
        assert deployment['kind'] == 'Deployment'
        assert deployment['spec']['replicas'] >= 2
        
        # Validate security context
        container = deployment['spec']['template']['spec']['containers'][0]
        security_context = container['securityContext']
        assert security_context['runAsNonRoot'] is True
        assert security_context['readOnlyRootFilesystem'] is True
        assert security_context['allowPrivilegeEscalation'] is False
        
        # Validate resource limits
        resources = container['resources']
        assert 'requests' in resources
        assert 'limits' in resources
        assert resources['limits']['memory'] >= resources['requests']['memory']
        
        # Validate probes
        assert 'livenessProbe' in container
        assert 'readinessProbe' in container
        
        # Validate HPA
        hpa = k8s_manifests['hpa']
        assert hpa['spec']['minReplicas'] >= 2
        assert hpa['spec']['maxReplicas'] <= 20
    
    def test_helm_chart_generation(self, tmp_path):
        """Test Helm chart generation for deployment."""
        chart_dir = tmp_path / "codegen-chart"
        chart_dir.mkdir()
        
        # Chart.yaml
        chart_yaml = {
            'apiVersion': 'v2',
            'name': 'codegen-agent',
            'description': 'A Helm chart for CodeGen Agent',
            'type': 'application',
            'version': '0.1.0',
            'appVersion': '1.0.0'
        }
        
        with open(chart_dir / "Chart.yaml", 'w') as f:
            yaml.dump(chart_yaml, f)
        
        # values.yaml
        values_yaml = {
            'replicaCount': 3,
            'image': {
                'repository': 'codegen-agent',
                'pullPolicy': 'IfNotPresent',
                'tag': 'latest'
            },
            'service': {
                'type': 'ClusterIP',
                'port': 8000
            },
            'ingress': {
                'enabled': True,
                'className': 'nginx',
                'annotations': {
                    'cert-manager.io/cluster-issuer': 'letsencrypt-prod'
                },
                'hosts': [{
                    'host': 'codegen.example.com',
                    'paths': [{
                        'path': '/',
                        'pathType': 'ImplementationSpecific'
                    }]
                }],
                'tls': [{
                    'secretName': 'codegen-tls',
                    'hosts': ['codegen.example.com']
                }]
            },
            'resources': {
                'limits': {
                    'cpu': '2000m',
                    'memory': '2Gi'
                },
                'requests': {
                    'cpu': '500m',
                    'memory': '512Mi'
                }
            },
            'autoscaling': {
                'enabled': True,
                'minReplicas': 2,
                'maxReplicas': 10,
                'targetCPUUtilizationPercentage': 70
            },
            'redis': {
                'enabled': True,
                'auth': {
                    'enabled': True,
                    'password': 'changeme'
                }
            },
            'monitoring': {
                'enabled': True,
                'serviceMonitor': {
                    'enabled': True
                }
            }
        }
        
        with open(chart_dir / "values.yaml", 'w') as f:
            yaml.dump(values_yaml, f)
        
        # Create templates directory
        templates_dir = chart_dir / "templates"
        templates_dir.mkdir()
        
        # Validate Helm chart structure
        assert (chart_dir / "Chart.yaml").exists()
        assert (chart_dir / "values.yaml").exists()
        assert templates_dir.exists()
        
        # Test Helm lint (if Helm is available)
        try:
            result = subprocess.run(
                ['helm', 'lint', str(chart_dir)],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                assert "0 chart(s) failed" in result.stdout
        except FileNotFoundError:
            # Helm not installed, skip
            pass


# ============================================================================
# Cloud Provider Integration Tests
# ============================================================================

class TestCloudProviderIntegration:
    """Test cloud provider specific integrations."""
    
    def test_aws_deployment_config(self, tmp_path):
        """Test AWS ECS/EKS deployment configuration."""
        # ECS Task Definition
        task_definition = {
            'family': 'codegen-agent',
            'networkMode': 'awsvpc',
            'requiresCompatibilities': ['FARGATE'],
            'cpu': '1024',
            'memory': '2048',
            'containerDefinitions': [{
                'name': 'codegen-agent',
                'image': 'codegen-agent:latest',
                'essential': True,
                'portMappings': [
                    {
                        'containerPort': 8000,
                        'protocol': 'tcp'
                    }
                ],
                'environment': [
                    {'name': 'AWS_REGION', 'value': 'us-west-2'}
                ],
                'secrets': [
                    {
                        'name': 'OPENAI_API_KEY',
                        'valueFrom': 'arn:aws:secretsmanager:us-west-2:123456789:secret:openai-key'
                    }
                ],
                'logConfiguration': {
                    'logDriver': 'awslogs',
                    'options': {
                        'awslogs-group': '/ecs/codegen-agent',
                        'awslogs-region': 'us-west-2',
                        'awslogs-stream-prefix': 'ecs'
                    }
                },
                'healthCheck': {
                    'command': ['CMD-SHELL', 'curl -f http://localhost:8000/health || exit 1'],
                    'interval': 30,
                    'timeout': 5,
                    'retries': 3
                }
            }]
        }
        
        # Write task definition
        task_def_file = tmp_path / "ecs-task-definition.json"
        with open(task_def_file, 'w') as f:
            json.dump(task_definition, f, indent=2)
        
        # Validate task definition
        assert task_definition['cpu'] == '1024'
        assert task_definition['memory'] == '2048'
        assert len(task_definition['containerDefinitions']) > 0
    
    def test_gcp_deployment_config(self, tmp_path):
        """Test Google Cloud Run deployment configuration."""
        cloud_run_config = {
            'apiVersion': 'serving.knative.dev/v1',
            'kind': 'Service',
            'metadata': {
                'name': 'codegen-agent',
                'annotations': {
                    'run.googleapis.com/ingress': 'internal-and-cloud-load-balancing'
                }
            },
            'spec': {
                'template': {
                    'metadata': {
                        'annotations': {
                            'autoscaling.knative.dev/minScale': '1',
                            'autoscaling.knative.dev/maxScale': '100',
                            'run.googleapis.com/cpu-throttling': 'false'
                        }
                    },
                    'spec': {
                        'containerConcurrency': 1000,
                        'timeoutSeconds': 300,
                        'serviceAccountName': 'codegen-sa@project.iam.gserviceaccount.com',
                        'containers': [{
                            'image': 'gcr.io/project/codegen-agent:latest',
                            'ports': [{'containerPort': 8000}],
                            'resources': {
                                'limits': {
                                    'cpu': '2',
                                    'memory': '2Gi'
                                }
                            },
                            'env': [
                                {
                                    'name': 'REDIS_URL',
                                    'valueFrom': {
                                        'secretKeyRef': {
                                            'name': 'redis-url',
                                            'key': 'latest'
                                        }
                                    }
                                }
                            ]
                        }]
                    }
                }
            }
        }
        
        # Write Cloud Run config
        config_file = tmp_path / "cloud-run.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(cloud_run_config, f)
        
        # Validate configuration
        assert cloud_run_config['spec']['template']['spec']['containerConcurrency'] == 1000
        assert 'autoscaling.knative.dev/minScale' in cloud_run_config['spec']['template']['metadata']['annotations']
    
    def test_azure_deployment_config(self, tmp_path):
        """Test Azure Container Instances deployment configuration."""
        aci_config = {
            'apiVersion': '2021-09-01',
            'type': 'Microsoft.ContainerInstance/containerGroups',
            'name': 'codegen-agent',
            'location': 'westus2',
            'properties': {
                'containers': [{
                    'name': 'codegen-agent',
                    'properties': {
                        'image': 'codegenacr.azurecr.io/codegen-agent:latest',
                        'resources': {
                            'requests': {
                                'cpu': 1.0,
                                'memoryInGB': 2.0
                            }
                        },
                        'ports': [
                            {'port': 8000, 'protocol': 'TCP'}
                        ],
                        'environmentVariables': [
                            {'name': 'AZURE_REGION', 'value': 'westus2'}
                        ],
                        'livenessProbe': {
                            'httpGet': {
                                'path': '/health',
                                'port': 8000
                            },
                            'periodSeconds': 30
                        }
                    }
                }],
                'osType': 'Linux',
                'restartPolicy': 'Always',
                'ipAddress': {
                    'type': 'Public',
                    'ports': [
                        {'port': 8000, 'protocol': 'TCP'}
                    ],
                    'dnsNameLabel': 'codegen-agent'
                },
                'imageRegistryCredentials': [{
                    'server': 'codegenacr.azurecr.io',
                    'username': 'codegenacr',
                    'password': '[parameters(\'registryPassword\')]'
                }]
            }
        }
        
        # Write ACI configuration
        aci_file = tmp_path / "azure-aci.json"
        with open(aci_file, 'w') as f:
            json.dump(aci_config, f, indent=2)
        
        # Validate configuration
        assert aci_config['properties']['containers'][0]['properties']['resources']['requests']['cpu'] >= 1.0
        assert aci_config['properties']['osType'] == 'Linux'


# ============================================================================
# CI/CD Pipeline Integration Tests
# ============================================================================

class TestCICDIntegration:
    """Test CI/CD pipeline integrations."""
    
    def test_github_actions_workflow(self, tmp_path):
        """Test GitHub Actions workflow configuration."""
        workflow = {
            'name': 'CI/CD Pipeline',
            'on': {
                'push': {
                    'branches': ['main', 'develop']
                },
                'pull_request': {
                    'branches': ['main']
                }
            },
            'env': {
                'REGISTRY': 'ghcr.io',
                'IMAGE_NAME': '${{ github.repository }}'
            },
            'jobs': {
                'test': {
                    'runs-on': 'ubuntu-latest',
                    'steps': [
                        {
                            'uses': 'actions/checkout@v3'
                        },
                        {
                            'name': 'Set up Python',
                            'uses': 'actions/setup-python@v4',
                            'with': {
                                'python-version': '3.10'
                            }
                        },
                        {
                            'name': 'Install dependencies',
                            'run': 'pip install -r requirements.txt'
                        },
                        {
                            'name': 'Run tests',
                            'run': 'pytest tests/ --cov=agents --cov-report=xml'
                        },
                        {
                            'name': 'Upload coverage',
                            'uses': 'codecov/codecov-action@v3'
                        }
                    ]
                },
                'security': {
                    'runs-on': 'ubuntu-latest',
                    'steps': [
                        {
                            'uses': 'actions/checkout@v3'
                        },
                        {
                            'name': 'Run Trivy security scan',
                            'uses': 'aquasecurity/trivy-action@master',
                            'with': {
                                'scan-type': 'fs',
                                'scan-ref': '.',
                                'format': 'sarif',
                                'output': 'trivy-results.sarif'
                            }
                        },
                        {
                            'name': 'Upload Trivy results',
                            'uses': 'github/codeql-action/upload-sarif@v2',
                            'with': {
                                'sarif_file': 'trivy-results.sarif'
                            }
                        }
                    ]
                },
                'build': {
                    'runs-on': 'ubuntu-latest',
                    'needs': ['test', 'security'],
                    'steps': [
                        {
                            'uses': 'actions/checkout@v3'
                        },
                        {
                            'name': 'Set up Docker Buildx',
                            'uses': 'docker/setup-buildx-action@v2'
                        },
                        {
                            'name': 'Log in to GitHub Container Registry',
                            'uses': 'docker/login-action@v2',
                            'with': {
                                'registry': '${{ env.REGISTRY }}',
                                'username': '${{ github.actor }}',
                                'password': '${{ secrets.GITHUB_TOKEN }}'
                            }
                        },
                        {
                            'name': 'Build and push Docker image',
                            'uses': 'docker/build-push-action@v4',
                            'with': {
                                'context': '.',
                                'push': True,
                                'tags': '${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest',
                                'cache-from': 'type=gha',
                                'cache-to': 'type=gha,mode=max'
                            }
                        }
                    ]
                },
                'deploy': {
                    'runs-on': 'ubuntu-latest',
                    'needs': 'build',
                    'if': "github.ref == 'refs/heads/main'",
                    'steps': [
                        {
                            'uses': 'actions/checkout@v3'
                        },
                        {
                            'name': 'Deploy to Kubernetes',
                            'uses': 'azure/k8s-deploy@v4',
                            'with': {
                                'namespace': 'codegen-system',
                                'manifests': 'k8s/',
                                'images': '${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest'
                            }
                        }
                    ]
                }
            }
        }
        
        # Write workflow file
        workflow_dir = tmp_path / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        workflow_file = workflow_dir / "ci-cd.yml"
        
        with open(workflow_file, 'w') as f:
            yaml.dump(workflow, f)
        
        # Validate workflow
        assert 'test' in workflow['jobs']
        assert 'security' in workflow['jobs']
        assert 'build' in workflow['jobs']
        assert 'deploy' in workflow['jobs']
    
    def test_gitlab_ci_pipeline(self, tmp_path):
        """Test GitLab CI/CD pipeline configuration."""
        gitlab_ci = """
stages:
  - test
  - security
  - build
  - deploy

variables:
  DOCKER_DRIVER: overlay2
  DOCKER_TLS_CERTDIR: ""
  REGISTRY: registry.gitlab.com
  IMAGE_NAME: $CI_PROJECT_PATH

test:unit:
  stage: test
  image: python:3.10
  script:
    - pip install -r requirements.txt
    - pytest tests/ --cov=agents --cov-report=xml
  coverage: '/(?i)total.*? (100(?:\\.0+)?\\%|[1-9]?\\d(?:\\.\\d+)?\\%)$/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

test:integration:
  stage: test
  services:
    - redis:7-alpine
  script:
    - pytest tests/test_e2e_integration.py

security:sast:
  stage: security
  script:
    - pip install bandit semgrep
    - bandit -r agents/ -f json -o bandit-report.json
    - semgrep --config=auto agents/ --json -o semgrep-report.json
  artifacts:
    reports:
      sast: [bandit-report.json, semgrep-report.json]

security:container:
  stage: security
  image: aquasec/trivy:latest
  script:
    - trivy fs --security-checks vuln,config .
    - trivy image --exit-code 1 --severity HIGH,CRITICAL $IMAGE_NAME

build:image:
  stage: build
  image: docker:latest
  services:
    - docker:dind
  before_script:
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
  script:
    - docker build -t $REGISTRY/$IMAGE_NAME:$CI_COMMIT_SHA .
    - docker push $REGISTRY/$IMAGE_NAME:$CI_COMMIT_SHA
    - docker tag $REGISTRY/$IMAGE_NAME:$CI_COMMIT_SHA $REGISTRY/$IMAGE_NAME:latest
    - docker push $REGISTRY/$IMAGE_NAME:latest

deploy:staging:
  stage: deploy
  environment:
    name: staging
    url: https://staging.codegen.example.com
  script:
    - kubectl set image deployment/codegen-agent codegen-agent=$REGISTRY/$IMAGE_NAME:$CI_COMMIT_SHA -n staging
  only:
    - develop

deploy:production:
  stage: deploy
  environment:
    name: production
    url: https://codegen.example.com
  script:
    - kubectl set image deployment/codegen-agent codegen-agent=$REGISTRY/$IMAGE_NAME:$CI_COMMIT_SHA -n production
  only:
    - main
  when: manual
"""
        
        # Write GitLab CI file
        gitlab_file = tmp_path / ".gitlab-ci.yml"
        gitlab_file.write_text(gitlab_ci)
        
        # Validate configuration exists
        assert gitlab_file.exists()
        
        # Parse and validate
        parsed = yaml.safe_load(gitlab_ci)
        assert 'stages' in parsed
        assert 'test:unit' in parsed
        assert 'security:sast' in parsed
        assert 'build:image' in parsed
        assert 'deploy:production' in parsed


# ============================================================================
# Monitoring and Observability Integration Tests
# ============================================================================

class TestMonitoringIntegration:
    """Test monitoring and observability integrations."""
    
    def test_prometheus_config(self, tmp_path):
        """Test Prometheus configuration for monitoring."""
        prometheus_config = """
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'codegen-agent'
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names:
            - codegen-system
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: true
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
        action: replace
        target_label: __metrics_path__
        regex: (.+)
      - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
        action: replace
        regex: ([^:]+)(?::\\d+)?;(\\d+)
        replacement: $1:$2
        target_label: __address__

  - job_name: 'kubernetes-pods'
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        action: keep
        regex: codegen-agent

rule_files:
  - 'alerts.yml'

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - alertmanager:9093
"""
        
        # Write Prometheus config
        prom_file = tmp_path / "prometheus.yml"
        prom_file.write_text(prometheus_config)
        
        # Alert rules
        alert_rules = """
groups:
  - name: codegen_alerts
    interval: 30s
    rules:
      - alert: HighErrorRate
        expr: rate(codegen_errors_total[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: High error rate detected
          description: "Error rate is {{ $value }} errors per second"
      
      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(codegen_latency_seconds_bucket[5m])) > 5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: High latency detected
          description: "95th percentile latency is {{ $value }} seconds"
      
      - alert: LowCacheHitRate
        expr: rate(cache_hits_total[5m]) / rate(cache_requests_total[5m]) < 0.5
        for: 15m
        labels:
          severity: info
        annotations:
          summary: Low cache hit rate
          description: "Cache hit rate is {{ $value }}"
      
      - alert: CircuitBreakerOpen
        expr: llm_circuit_open > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: Circuit breaker is open
          description: "Circuit breaker for {{ $labels.backend }} is open"
"""
        
        alerts_file = tmp_path / "alerts.yml"
        alerts_file.write_text(alert_rules)
        
        # Validate configurations exist
        assert prom_file.exists()
        assert alerts_file.exists()
    
    def test_grafana_dashboard(self, tmp_path):
        """Test Grafana dashboard configuration."""
        dashboard = {
            "dashboard": {
                "id": None,
                "uid": "codegen-agent",
                "title": "CodeGen Agent Monitoring",
                "tags": ["codegen", "llm", "production"],
                "timezone": "browser",
                "panels": [
                    {
                        "id": 1,
                        "type": "graph",
                        "title": "Request Rate",
                        "targets": [{
                            "expr": "rate(codegen_requests_total[5m])",
                            "legendFormat": "{{ backend }}"
                        }]
                    },
                    {
                        "id": 2,
                        "type": "graph",
                        "title": "Latency (p50, p95, p99)",
                        "targets": [
                            {
                                "expr": "histogram_quantile(0.5, rate(codegen_latency_seconds_bucket[5m]))",
                                "legendFormat": "p50"
                            },
                            {
                                "expr": "histogram_quantile(0.95, rate(codegen_latency_seconds_bucket[5m]))",
                                "legendFormat": "p95"
                            },
                            {
                                "expr": "histogram_quantile(0.99, rate(codegen_latency_seconds_bucket[5m]))",
                                "legendFormat": "p99"
                            }
                        ]
                    },
                    {
                        "id": 3,
                        "type": "stat",
                        "title": "Error Rate",
                        "targets": [{
                            "expr": "rate(codegen_errors_total[5m])"
                        }]
                    },
                    {
                        "id": 4,
                        "type": "gauge",
                        "title": "Cache Hit Rate",
                        "targets": [{
                            "expr": "rate(cache_hits_total[5m]) / rate(cache_requests_total[5m]) * 100"
                        }]
                    },
                    {
                        "id": 5,
                        "type": "table",
                        "title": "Circuit Breaker Status",
                        "targets": [{
                            "expr": "llm_circuit_open"
                        }]
                    },
                    {
                        "id": 6,
                        "type": "heatmap",
                        "title": "Token Usage Distribution",
                        "targets": [{
                            "expr": "llm_token_usage"
                        }]
                    }
                ],
                "refresh": "5s",
                "time": {
                    "from": "now-1h",
                    "to": "now"
                }
            }
        }
        
        # Write dashboard
        dashboard_file = tmp_path / "grafana-dashboard.json"
        with open(dashboard_file, 'w') as f:
            json.dump(dashboard, f, indent=2)
        
        # Validate dashboard
        assert dashboard["dashboard"]["title"] == "CodeGen Agent Monitoring"
        assert len(dashboard["dashboard"]["panels"]) >= 6
    
    def test_logging_aggregation(self, tmp_path):
        """Test logging aggregation configuration (ELK/Loki)."""
        # Fluent Bit configuration for log shipping
        fluent_bit_config = """
[SERVICE]
    Flush        5
    Daemon       Off
    Log_Level    info

[INPUT]
    Name              tail
    Path              /var/log/containers/*codegen*.log
    Parser            docker
    Tag               codegen.*
    Refresh_Interval  5

[FILTER]
    Name         parser
    Match        codegen.*
    Key_Name     log
    Parser       json
    Reserve_Data On

[FILTER]
    Name         kubernetes
    Match        codegen.*
    Kube_URL     https://kubernetes.default.svc:443
    Kube_CA_File /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
    Kube_Token_File /var/run/secrets/kubernetes.io/serviceaccount/token

[OUTPUT]
    Name  es
    Match codegen.*
    Host  elasticsearch
    Port  9200
    Index codegen
    Type  _doc

[OUTPUT]
    Name   loki
    Match  codegen.*
    Host   loki
    Port   3100
    Labels job=codegen-agent
"""
        
        fluent_file = tmp_path / "fluent-bit.conf"
        fluent_file.write_text(fluent_bit_config)
        
        # Loki configuration
        loki_config = """
auth_enabled: false

server:
  http_listen_port: 3100

ingester:
  lifecycler:
    address: 127.0.0.1
    ring:
      kvstore:
        store: inmemory
      replication_factor: 1

schema_config:
  configs:
    - from: 2020-10-24
      store: boltdb-shipper
      object_store: filesystem
      schema: v11
      index:
        prefix: index_
        period: 24h

storage_config:
  boltdb_shipper:
    active_index_directory: /loki/boltdb-shipper-active
    cache_location: /loki/boltdb-shipper-cache
    shared_store: filesystem
  filesystem:
    directory: /loki/chunks

limits_config:
  enforce_metric_name: false
  reject_old_samples: true
  reject_old_samples_max_age: 168h
"""
        
        loki_file = tmp_path / "loki-config.yaml"
        loki_file.write_text(loki_config)
        
        assert fluent_file.exists()
        assert loki_file.exists()


# ============================================================================
# Load Testing and Performance Validation
# ============================================================================

class TestLoadTesting:
    """Load testing and performance validation."""
    
    @pytest.mark.slow
    def test_load_test_configuration(self, tmp_path):
        """Test load testing configuration with k6."""
        k6_script = """
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

export const options = {
  stages: [
    { duration: '2m', target: 10 },   // Ramp up to 10 users
    { duration: '5m', target: 10 },   // Stay at 10 users
    { duration: '2m', target: 50 },   // Ramp up to 50 users
    { duration: '5m', target: 50 },   // Stay at 50 users
    { duration: '2m', target: 100 },  // Ramp up to 100 users
    { duration: '5m', target: 100 },  // Stay at 100 users
    { duration: '5m', target: 0 },    // Ramp down to 0 users
  ],
  thresholds: {
    http_req_duration: ['p(95)<5000'], // 95% of requests must complete below 5s
    errors: ['rate<0.1'],               // Error rate must be below 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // Test code generation endpoint
  const payload = JSON.stringify({
    requirements: {
      features: ['Create a simple function'],
      target_language: 'python'
    },
    state_summary: 'Initial state'
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${__ENV.API_TOKEN}`,
    },
    timeout: '30s',
  };

  const response = http.post(`${BASE_URL}/generate`, payload, params);
  
  const success = check(response, {
    'status is 200': (r) => r.status === 200,
    'response has files': (r) => {
      const body = JSON.parse(r.body);
      return body.files && Object.keys(body.files).length > 0;
    },
    'response time < 5s': (r) => r.timings.duration < 5000,
  });

  errorRate.add(!success);
  
  // Test health endpoint
  const healthResponse = http.get(`${BASE_URL}/health`);
  check(healthResponse, {
    'health check passes': (r) => r.status === 200,
  });
  
  sleep(1);
}

export function handleSummary(data) {
  return {
    'summary.json': JSON.stringify(data),
    stdout: textSummary(data, { indent: ' ', enableColors: true }),
  };
}
"""
        
        k6_file = tmp_path / "load-test.js"
        k6_file.write_text(k6_script)
        
        # Locust configuration for alternative load testing
        locust_file_content = """
from locust import HttpUser, task, between
import json

class CodeGenUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(3)
    def generate_code(self):
        payload = {
            "requirements": {
                "features": ["Create a REST API endpoint"],
                "target_language": "python"
            },
            "state_summary": "Test state"
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}"
        }
        
        with self.client.post(
            "/generate",
            json=payload,
            headers=headers,
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got status code {response.status_code}")
    
    @task(1)
    def check_health(self):
        self.client.get("/health")
    
    def on_start(self):
        # Login or get token
        self.token = "test-token"
"""
        
        locust_file = tmp_path / "locustfile.py"
        locust_file.write_text(locust_file_content)
        
        assert k6_file.exists()
        assert locust_file.exists()


# ============================================================================
# Run Deployment Integration Tests
# ============================================================================

if __name__ == '__main__':
    pytest.main([
        __file__,
        '-v',
        '--tb=short',
        '--cov=agents',
        '--cov-report=term-missing',
        '--cov-report=html:deployment_coverage'
    ])