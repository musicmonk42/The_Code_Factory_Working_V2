# test_parser_priorities_config.py

import pytest
import json
from pathlib import Path

class TestParserPrioritiesConfig:
    """Test the parser_priorities.json configuration file."""
    
    @pytest.fixture
    def config_data(self):
        """Load the actual parser_priorities.json file."""
        config_path = Path(__file__).parent.parent / "parser_priorities.json"
        with open(config_path, 'r') as f:
            return json.load(f)
    
    def test_config_structure(self, config_data):
        """Test the overall structure of the config."""
        assert "configuration_version" in config_data
        assert "parser_priorities" in config_data
        assert "default_settings" in config_data
        assert "domain_specific_overrides" in config_data
        assert "parser_chains" in config_data
        assert "performance_thresholds" in config_data
        assert "monitoring" in config_data
    
    def test_parser_priority_schema(self, config_data):
        """Test each parser priority entry has required fields."""
        for parser_name, config in config_data["parser_priorities"].items():
            assert "priority" in config, f"{parser_name} missing priority"
            assert "description" in config, f"{parser_name} missing description"
            assert "enabled" in config, f"{parser_name} missing enabled"
            assert isinstance(config["priority"], int)
            assert isinstance(config["enabled"], bool)
            
            # Critical parsers should have shorter timeouts for faster failure
            if config.get("critical", False):
                if "timeout_override" in config:
                    # Critical parsers should have reasonable timeouts (not necessarily short)
                    assert config["timeout_override"] <= 10, \
                        f"Critical parser {parser_name} has too long timeout"
                
                # Note: Critical doesn't necessarily mean high priority
                # Some critical parsers (like CreditCardParser) are specialized
                # and run after more general security parsers
    
    def test_priority_ordering(self, config_data):
        """Test that security-critical parsers have appropriate priority."""
        parsers = config_data["parser_priorities"]
        
        # Security parser should be highest
        assert parsers["SecurityEventParser"]["priority"] == 1000
        assert parsers["SecurityEventParser"]["critical"] is True
        
        # Compliance should be very high
        assert parsers["ComplianceDataParser"]["priority"] >= 900
        
        # Generic parser should be lowest
        assert parsers["GenericTextParser"]["priority"] < 100
        
        # Specialized critical parsers (SSN, Credit Card) can have lower base priority
        # They're critical for data handling but specialized in scope
        assert parsers["CreditCardParser"]["critical"] is True
        assert parsers["SocialSecurityNumberParser"]["critical"] is True
    
    def test_timeout_overrides(self, config_data):
        """Test timeout overrides are reasonable."""
        for parser_name, config in config_data["parser_priorities"].items():
            if "timeout_override" in config:
                timeout = config["timeout_override"]
                assert 0 < timeout <= 30, f"{parser_name} has unreasonable timeout: {timeout}"
                
                # Critical parsers should have reasonable timeouts
                if config.get("critical", False):
                    # Allow up to 10 seconds for critical parsers
                    assert timeout <= 10, f"Critical parser {parser_name} timeout too long: {timeout}"
    
    def test_domain_specific_overrides(self, config_data):
        """Test domain-specific priority overrides."""
        overrides = config_data["domain_specific_overrides"]
        
        # Financial domain should prioritize financial parsers
        assert "FinancialTransactionParser" in overrides.get("FinancialData", {})
        assert overrides["FinancialData"]["FinancialTransactionParser"] >= 900
        
        # Personal data should prioritize PII parser
        assert "PersonalIdentifiableInformationParser" in overrides.get("PersonalData", {})
        assert overrides["PersonalData"]["PersonalIdentifiableInformationParser"] >= 900
        
        # In PersonalData domain, specialized parsers get boosted priority
        assert overrides["PersonalData"]["CreditCardParser"] >= 900
        assert overrides["PersonalData"]["SocialSecurityNumberParser"] >= 900
    
    def test_parser_chains(self, config_data):
        """Test parser chain configurations."""
        chains = config_data["parser_chains"]
        
        # Each chain should have valid parsers
        for chain_name, parser_list in chains.items():
            assert len(parser_list) > 0, f"Empty chain: {chain_name}"
            for parser in parser_list:
                assert parser in config_data["parser_priorities"], \
                    f"Unknown parser {parser} in chain {chain_name}"
        
        # PII detection chain should include all PII-related parsers
        pii_chain = chains["pii_detection"]
        assert "PersonalIdentifiableInformationParser" in pii_chain
        assert "SocialSecurityNumberParser" in pii_chain
        assert "CreditCardParser" in pii_chain
    
    def test_performance_thresholds(self, config_data):
        """Test performance threshold configurations."""
        thresholds = config_data["performance_thresholds"]
        
        assert thresholds["max_total_parsing_time_ms"] > 0
        assert thresholds["max_single_parser_time_ms"] > 0
        assert 0 <= thresholds["min_success_rate_percent"] <= 100
        assert thresholds["max_memory_usage_mb"] > 0
        
        # Single parser time should be less than total time
        assert thresholds["max_single_parser_time_ms"] < thresholds["max_total_parsing_time_ms"]
    
    def test_critical_parser_properties(self, config_data):
        """Test that critical parsers have appropriate properties."""
        critical_parsers = [
            name for name, config in config_data["parser_priorities"].items()
            if config.get("critical", False)
        ]
        
        # Should have at least some critical parsers
        assert len(critical_parsers) > 0, "No critical parsers defined"
        
        for parser_name in critical_parsers:
            config = config_data["parser_priorities"][parser_name]
            
            # Critical parsers should be enabled
            assert config["enabled"], f"Critical parser {parser_name} is disabled"
            
            # Critical parsers should have timeout overrides
            assert "timeout_override" in config, \
                f"Critical parser {parser_name} missing timeout override"
            
            # Check that critical parsers appear in appropriate chains or overrides
            # They should be prioritized in at least one domain or chain
            in_chain = any(
                parser_name in chain 
                for chain in config_data["parser_chains"].values()
            )
            in_override = any(
                parser_name in domain 
                for domain in config_data["domain_specific_overrides"].values()
            )
            assert in_chain or in_override, \
                f"Critical parser {parser_name} not used in any chain or override"