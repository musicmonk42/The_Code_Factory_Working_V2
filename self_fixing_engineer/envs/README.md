# Envs Module – Production-Ready RL & GA Optimization

## Overview

**Envs** provides reinforcement learning (RL) and genetic algorithm (GA) environments for system optimization and configuration tuning. It offers thread-safe, production-grade implementations with comprehensive monitoring, auditing, and safety features.

---

## 🚀 Key Components

### 1. `CodeHealthEnv` (`code_health_env.py`)
A [Gymnasium](https://gymnasium.farama.org/)-compatible RL environment for monitoring and optimizing system health metrics.

**Features:**
- **Thread-Safe Operations**: Full thread safety with `RLock`
- **Async/Sync Support**: Handles both async and sync actions seamlessly
- **Memory Management**: Bounded history with configurable limits via `deque`
- **Automatic Safety**: Multi-tier thresholds & auto rollback on critical states
- **Action Cooldowns**: Configurable cooldowns to prevent spamming
- **Comprehensive Metrics**: Tracks `pass_rate`, `latency`, `alert_ratio`, `code_coverage`, `complexity`
- **Multiple Render Modes**: Human, RGB array, ANSI colored output

---

### 2. `Evolution` Module (`evolution.py`)
A genetic algorithm optimizer for discovering optimal system configurations.

**Features:**
- **Encapsulated Design**: No global namespace pollution; isolated DEAP usage
- **Parallel Evaluation**: Thread pool for concurrent fitness calculations
- **Caching System**: Thread-safe evaluation cache
- **Sandboxed Execution**: Secure subprocess for fitness evaluation
- **Early Stopping**: Auto-terminate on fitness plateau
- **Checkpoint Support**: Save/load state for long-running runs
- **Configurable Search Space**: Flexible parameter definitions with type support

---

## 📦 Installation

```bash
# Clone repository
git clone <repository-url>
cd envs

# Create virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

**Requirements**
```txt
gymnasium>=0.26.0
numpy>=1.21.0
matplotlib>=3.4.0
deap>=1.3.3
termcolor>=1.1.0
pytest>=7.0.0
pytest-asyncio>=0.21.0
pytest-timeout>=2.1.0
```

---

## ⚡ Quick Start

### Using `CodeHealthEnv`
```python
from envs.code_health_env import CodeHealthEnv, EnvironmentConfig, SystemMetrics

config = EnvironmentConfig(
    max_steps=100,
    unacceptable_threshold=0.4,
    critical_threshold=0.2,
    enable_auto_rollback=True
)

env = CodeHealthEnv(
    get_metrics=lambda: SystemMetrics(pass_rate=0.9, latency=0.1),
    apply_action=lambda a: {"success": True},
    config=config
)

obs, info = env.reset()
for _ in range(100):
    action = env.action_space.sample()
    obs, reward, done, info = env.step(action)
    if done:
        break

env.close()
```

### Using `GeneticOptimizer`
```python
from envs.evolution import GeneticOptimizer, EvolutionConfig

config = EvolutionConfig(
    generations=20,
    population_size=50,
    crossover_probability=0.8,
    mutation_probability=0.3
)

optimizer = GeneticOptimizer(evolution_config=config)

def fitness_function(individual):
    return sum(individual)  # Example

best_config = optimizer.evolve(
    test_function=fitness_function,
    verbose=True
)
```

---

## 🏛️ Architecture

### Thread Safety
- Shared state protected with `threading.RLock`
- Thread-safe cache with locking
- Async executor via dedicated thread pool

### Memory Management
- Bounded history with `collections.deque`
- Configurable history limits
- Automatic cleanup on `close()`

### Safety Features
- Two-tier threshold system (unacceptable/critical)
- Automatic rollback on degraded states
- Action cooldowns to prevent thrashing
- Graceful degradation on errors

---

## ⚙️ Configuration

### Environment Configuration
```python
@dataclass
class EnvironmentConfig:
    observation_keys: List[str]
    max_steps: int = 100
    unacceptable_threshold: float = 0.4
    critical_threshold: float = 0.2
    enable_auto_rollback: bool = True
    max_action_history: int = 1000
    action_cooldowns: Dict[int, int]
```

### Evolution Configuration
```python
@dataclass
class EvolutionConfig:
    generations: int = 10
    population_size: int = 20
    crossover_probability: float = 0.7
    mutation_probability: float = 0.2
    elite_size: int = 2
    cache_evaluations: bool = True
    early_stopping_generations: int = 5
```

---

## 🧪 Testing

### Run All Tests
```bash
pytest envs/tests -v
```

### Run Specific Suites
```bash
# Unit tests
pytest envs/tests/test_code_health_env.py -v
pytest envs/tests/test_evolution.py -v

# End-to-end tests
pytest envs/tests/test_e2e_env.py -v

# With coverage
pytest envs/tests --cov=envs --cov-report=html
```

**Coverage:**
- Unit: Core features, edge cases, error handling
- Integration: Component interaction, async ops
- E2E: Complete workflows, production scenarios

---

## 📊 Monitoring & Observability

### Metrics Export
```python
training_data = env.get_training_data()
summary = env.get_metrics_summary()
print(f"Mean pass rate: {summary['mean']['pass_rate']}")
print(f"Improvement: {summary['improvement']}")
```

### Visualization
```python
env.render(mode='human')        # Console output
rgb_array = env.render(mode='rgb_array')  # For video
env.render(mode='ansi')        # Colored terminal
```

---

## 🚢 Production Deployment

**Best Practices**
- Use `EnvironmentConfig` & `EvolutionConfig`
- Enable safety features (`auto_rollback`)
- Monitor resource usage (`max_action_history`)
- Handle async actions properly
- Implement real metrics functions in production

---

## ⚡ Performance Tuning

```python
# Optimize for high-frequency monitoring
config = EnvironmentConfig(
    max_action_history=100,
    action_cooldowns={1: 10, 2: 20},
)

# Parallel GA evaluation
evolution_config = EvolutionConfig(
    max_parallel_evaluations=8,
    cache_evaluations=True,
)
```

---

## 🛠️ Troubleshooting

**Common Issues**
- **Tests Hanging**: Check audit logger async handling
- **Memory Growth**: Verify history limits
- **Import Errors**: Ensure `PYTHONPATH` includes project root
- **DEAP Not Found**: `pip install deap`

**Debug Mode**
```python
import logging
logging.basicConfig(level=logging.DEBUG)
env = CodeHealthEnv(..., config=config)
```

---

## 📚 API Reference

### CodeHealthEnv
- `reset() -> Tuple[np.ndarray, Dict]`: Reset environment
- `step(action) -> Tuple[obs, reward, done, info]`: Execute action
- `render(mode) -> Optional[np.ndarray]`: Visualize state
- `close() -> None`: Clean up resources
- `get_training_data() -> List[Dict]`: Export history
- `get_metrics_summary() -> Dict`: Get statistics

### GeneticOptimizer
- `evolve(test_function, verbose) -> Dict`: Run evolution
- `get_evolution_summary() -> Dict`: Get results
- `save_checkpoint(filepath) -> None`: Save state
- `load_checkpoint(filepath) -> None`: Load state

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

---

## 📄 License

See `LICENSE` in the repository root.

---

## 💬 Support

For issues or questions, please file an issue on the repository.

---

> This README provides comprehensive documentation for the enhanced Envs module, including all production-ready features: thread safety, memory management, and thorough testing procedures.