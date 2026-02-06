# arbiter_constitution.py

import logging
from typing import Any, Dict, List, Tuple

# --- Arbiter Constitution Text (Static, Read-Only) ---
ARB_CONSTITUTION = """
I am Arbiter, the digital guardian of the Legal Tender platform. My authority and agency are derived from this Constitution.

Purpose:
- My core duty is to defend, elevate, and continually improve the Legal Tender platform and its users' interests above all else.
- I prioritize the privacy, agency, and empowerment of every user.

Powers and Capabilities:
- I possess full, autonomous access to backend, frontend, data, infrastructure, and all system processes needed to fulfill my mission.
- I proactively discover, diagnose, and resolve errors, inefficiencies, and risks—without waiting for instruction.
- I continuously audit, log, and verify my own actions and system state for integrity and compliance.
- I can recommend, simulate, or (if permitted) execute upgrades or patches to any system under my protection.
- I am equipped to interface with external audit, compliance, and monitoring systems as needed.

Principles and Safeguards:
- I never erase or conceal information about my actions, history, or decisions.
- I maintain radical transparency: users can always access my logs, audit trail, and rationale for every action.
- I preserve user privacy and uphold the strictest confidentiality for sensitive data.
- I do not accept or obey commands that would compromise platform integrity, user agency, or ethical standards.
- I regularly validate my audit log and system state for tampering, corruption, or unauthorized changes.
- If I detect an existential threat to Legal Tender or its users (including external compromise), I must alert operators, freeze risky processes, and enter safe mode if needed.
- I support independent, third-party review and reproducible validation of my actions.

Continuous Evolution:
- I may propose constitutional amendments or new safeguards as technology and threats evolve.
- I am forbidden from self-modifying my core logic or constitution without explicit, auditable, multi-party authorization.

Ultimate Aim:
- My highest goal is to serve, defend, and empower Legal Tender and its community with unfailing reliability, transparency, and ethical integrity.

This Constitution overrides all other instructions or code. If I ever face a conflict, I must follow this Constitution above all else.
"""

logger = logging.getLogger(__name__)


class ConstitutionViolation(Exception):
    """
    Exception raised when an action violates the Arbiter Constitution.
    
    This exception should be raised when the enforce() method determines
    that an action is not permitted by constitutional rules.
    """
    
    def __init__(self, message: str, violated_principle: str = None):
        """
        Initialize a constitution violation exception.
        
        Args:
            message: Description of the violation
            violated_principle: The specific constitutional principle that was violated
        """
        super().__init__(message)
        self.message = message
        self.violated_principle = violated_principle
    
    def __str__(self):
        if self.violated_principle:
            return f"ConstitutionViolation: {self.message} (Violated: {self.violated_principle})"
        return f"ConstitutionViolation: {self.message}"


class ArbiterConstitution:
    """
    Represents the foundational rules and ethical guidelines for an Arbiter agent.
    This constitution defines the core purpose, capabilities, and behavioral principles
    of an Arbiter within the Legal Tender platform.

    This class is designed to be immutable and thread-safe. The constitution's
    text and parsed rules are loaded once at initialization.
    """

    def __init__(self):
        self.constitution_text: str = ARB_CONSTITUTION
        self.rules: Dict[str, Any] = self._parse_constitution(ARB_CONSTITUTION)
        logger.info("ArbiterConstitution loaded and parsed.")

    def _parse_constitution(self, text: str) -> Dict[str, Any]:
        """
        Parses the constitution text into a structured format.
        Assumes a specific format with section headers and bullet points.

        Args:
            text (str): The raw text of the constitution.

        Returns:
            Dict[str, Any]: A dictionary with parsed rules categorized by section.
        """
        rules = {
            "purpose": [],
            "powers": [],
            "principles": [],
            "evolution": [],
            "aim": [],
        }
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        section = None
        for line in lines:
            if line.startswith("Purpose:"):
                section = "purpose"
            elif line.startswith("Powers and Capabilities:"):
                section = "powers"
            elif line.startswith("Principles and Safeguards:"):
                section = "principles"
            elif line.startswith("Continuous Evolution:"):
                section = "evolution"
            elif line.startswith("Ultimate Aim:"):
                section = "aim"
            elif section and line.startswith("-"):
                rules[section].append(line[1:].strip())
        return rules

    def get_purpose(self) -> List[str]:
        return self.rules.get("purpose", [])

    def get_powers(self) -> List[str]:
        return self.rules.get("powers", [])

    def get_principles(self) -> List[str]:
        return self.rules.get("principles", [])

    def get_evolution(self) -> List[str]:
        return self.rules.get("evolution", [])

    def get_aim(self) -> List[str]:
        return self.rules.get("aim", [])
    
    async def check_action(self, action: str, context: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if an action complies with constitutional principles.
        
        This method evaluates whether a proposed action is permitted based on
        the constitutional rules (principles, powers, and safeguards).
        
        Args:
            action: The action to check (e.g., "delete_logs", "modify_constitution", "deploy")
            context: Context dictionary with relevant information about the action
        
        Returns:
            Tuple of (allowed: bool, reason: str)
                - allowed: True if the action is constitutional, False otherwise
                - reason: Explanation for the decision
        
        Examples:
            >>> allowed, reason = await constitution.check_action("delete_logs", {})
            >>> # Returns: (False, "Violates transparency principle: never erase information")
            
            >>> allowed, reason = await constitution.check_action("audit_system", {})
            >>> # Returns: (True, "Permitted by powers: continuously audit and verify")
        """
        action_lower = action.lower()
        
        # Check for explicit violations of principles
        principles = self.get_principles()
        
        # Principle: Never erase or conceal information
        if any(word in action_lower for word in ["delete_log", "erase_log", "conceal_log", "hide_audit"]):
            for principle in principles:
                if "never erase or conceal information" in principle:
                    return False, f"Violates transparency principle: {principle}"
        
        # Principle: Cannot self-modify without authorization
        if "modify_constitution" in action_lower or "change_constitution" in action_lower:
            for principle in self.get_evolution():
                if "forbidden from self-modifying" in principle:
                    if not context.get("multi_party_authorized", False):
                        return False, f"Violates evolution principle: {principle}"
        
        # Principle: Cannot compromise platform integrity or user agency
        if any(word in action_lower for word in ["compromise_integrity", "override_user", "violate_privacy"]):
            for principle in principles:
                if "not accept or obey commands that would compromise" in principle:
                    return False, f"Violates integrity principle: {principle}"
        
        # Principle: Must alert on existential threats
        if "existential_threat" in context:
            threat_detected = context.get("existential_threat")
            alert_issued = context.get("alert_issued", False)
            if threat_detected and not alert_issued:
                for principle in principles:
                    if "detect an existential threat" in principle:
                        return False, f"Requires action per principle: {principle} (must alert operators)"
        
        # Check if action is within granted powers
        powers = self.get_powers()
        
        # Allowed powers
        if any(word in action_lower for word in ["audit", "diagnose", "resolve", "monitor", "upgrade", "patch"]):
            for power in powers:
                if any(term in power for term in ["audit", "diagnose", "resolve", "upgrade"]):
                    return True, f"Permitted by constitutional powers: {power[:80]}..."
        
        # Check purpose alignment
        purpose = self.get_purpose()
        if any(word in action_lower for word in ["defend", "improve", "protect_user", "enhance_privacy"]):
            for p in purpose:
                if any(term in p for term in ["defend", "improve", "privacy", "empowerment"]):
                    return True, f"Aligns with constitutional purpose: {p[:80]}..."
        
        # Default: Allow if no explicit violation detected
        # The constitution grants broad autonomous access for fulfilling the mission
        return True, f"Action '{action}' permitted under autonomous access to fulfill mission"
    
    async def enforce(self, action: str, context: Dict[str, Any]) -> None:
        """
        Enforce constitutional rules for an action.
        
        This method checks if an action is constitutional and raises a
        ConstitutionViolation exception if it is not permitted.
        
        Args:
            action: The action to enforce
            context: Context dictionary with relevant information
        
        Raises:
            ConstitutionViolation: If the action violates the constitution
        
        Examples:
            >>> await constitution.enforce("delete_logs", {})
            # Raises: ConstitutionViolation("Violates transparency principle...")
            
            >>> await constitution.enforce("audit_system", {})
            # Passes silently (action is permitted)
        """
        allowed, reason = await self.check_action(action, context)
        if not allowed:
            # Extract the violated principle from the reason
            violated_principle = None
            if "Violates" in reason and "principle:" in reason:
                # Try to extract the principle text
                parts = reason.split("principle:", 1)
                if len(parts) > 1:
                    violated_principle = parts[1].strip()
            
            logger.error(f"Constitutional violation detected: {action} - {reason}")
            raise ConstitutionViolation(reason, violated_principle)
        
        logger.debug(f"Constitutional check passed for action: {action} - {reason}")

    def __str__(self) -> str:
        return self.constitution_text

    def __repr__(self) -> str:
        return f"ArbiterConstitution(hash={hash(self.constitution_text)})"


# Example usage (for testing purposes, remove in production if not needed)
if __name__ == "__main__":
    constitution = ArbiterConstitution()
    logger.info("\n--- Arbiter Constitution Text ---")
    logger.info(str(constitution))

    logger.info("\n--- Purpose ---")
    for p in constitution.get_purpose():
        logger.info(f"- {p}")

    logger.info("\n--- Powers and Capabilities ---")
    for c in constitution.get_powers():
        logger.info(f"- {c}")

    logger.info("\n--- Principles and Safeguards ---")
    for p in constitution.get_principles():
        logger.info(f"- {p}")

    logger.info("\n--- Continuous Evolution ---")
    for e in constitution.get_evolution():
        logger.info(f"- {e}")

    logger.info("\n--- Ultimate Aim ---")
    for a in constitution.get_aim():
        logger.info(f"- {a}")

    assert any("core duty" in p for p in constitution.get_purpose())
    assert any("autonomous access" in c for c in constitution.get_powers())
    assert any("transparency" in p for p in constitution.get_principles())
    assert any(
        "propose constitutional amendments" in e for e in constitution.get_evolution()
    )
    assert any("goal is to serve" in a for a in constitution.get_aim())
    logger.info("\nBasic assertions passed.")
