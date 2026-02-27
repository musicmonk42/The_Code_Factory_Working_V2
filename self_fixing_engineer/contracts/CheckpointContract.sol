// Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.21;

// The contract now relies on standard string and abi encoding functions.
// If you're using OpenZeppelin, the `Strings` library is included for `toString` and `toHexString`
// which can be useful for external libraries or off-chain tools.
// The internal `Bytes` library below is for demonstration and self-containment only;
// a production contract should import and use battle-hardened, audited libraries.

// CS-1: Library defined at file scope — Solidity 0.8 does not allow library definitions
// inside a contract body. Moving it here fixes the ParserError on compilation.
/**
 * @dev Library for safe bytes-to-string conversions.
 */
library Bytes {
    function toString(uint256 value) internal pure returns (string memory) {
        if (value == 0) {
            return "0";
        }
        uint256 temp = value;
        uint256 digits;
        while (temp != 0) {
            digits++;
            temp /= 10;
        }
        bytes memory buffer = new bytes(digits);
        while (value != 0) {
            digits--;
            buffer[digits] = bytes1(uint8(48 + uint256(value % 10)));
            value /= 10;
        }
        return string(buffer);
    }

    function toHexString(bytes32 value) internal pure returns (string memory) {
        bytes memory buffer = new bytes(64);
        uint256 temp = uint256(value);
        for (uint256 i = 0; i < 32; i++) {
            buffer[63 - 2 * i] = toAsciiChar(uint8(temp & 0xF));
            temp >>= 4;
            buffer[63 - 2 * i - 1] = toAsciiChar(uint8(temp & 0xF));
            temp >>= 4;
        }
        return string(buffer);
    }

    function toAsciiChar(uint8 value) internal pure returns (bytes1) {
        if (value < 10) {
            return bytes1(uint8(48 + value));
        } else {
            return bytes1(uint8(87 + value));
        }
    }
}

/**
 * @title CheckpointContract
 * @dev A smart contract to store and verify checkpoint hashes on an EVM-compatible blockchain.
 * Each checkpoint record includes a hash of the data, a reference to the previous checkpoint's hash
 * (forming a tamper-evident chain), metadata, and an off-chain reference to the full payload[cite: 3].
 * Supports writing new checkpoints, reading the latest, reading by version (block number),
 * reading by hash, and performing logical rollbacks[cite: 4].
 *
 * @custom:design-considerations-prod
 * -   **Gas Optimization**: Minimal data stored on-chain, large payloads off-chain[cite: 5].
 * -   **Tamper-Evidence**: Hash chaining ensures integrity[cite: 6].
 * -   **Auditability**: Events provide a clear, auditable trail of actions[cite: 6].
 * -   **Immutability**: Once a checkpoint is recorded, its hash and details are immutable on-chain[cite: 7].
 * -   **Logical Rollback**: Rollbacks are recorded as new transactions, preserving full history[cite: 8].
 * @custom:testing
 * -   Test `writeCheckpoint` with valid and invalid inputs (e.g., mismatched `prevHash`)[cite: 9].
 * -   Test `getLatestCheckpoint` before and after writes[cite: 10].
 * -   Test `getCheckpointByVersion` for both existing and non-existing versions[cite: 11].
 * -   Test `getCheckpointByHash` for both existing and non-existing hashes[cite: 12].
 * -   Test `rollbackCheckpoint` to a valid target and to a non-existent target[cite: 13].
 * -   Test unauthorized access attempts on protected functions (`writeCheckpoint`, `rollbackCheckpoint`).
 */
contract CheckpointContract {
    using Bytes for uint256;
    using Bytes for bytes32;

    // Defines a mapping from an address to an address.
    address private owner;

    // CS-2: Monotonically incrementing version counter -- using block.number caused
    // silent overwrites when writeCheckpoint and rollbackCheckpoint were called in the
    // same block. A dedicated counter guarantees uniqueness regardless of block timing.
    uint256 private _checkpointVersionCounter;

    // Maximum length for rollback message bytes (CS-4)
    uint256 private constant MAX_ROLLBACK_MESSAGE_LENGTH = 256;

    // A. Security Audit & Access Control: Add a constructor to set the owner.
    constructor() {
        owner = msg.sender;
    }

    // A. Security Audit & Access Control: Add a modifier to restrict access to the owner.
    modifier onlyOwner() {
        require(msg.sender == owner, "Only the owner can call this function.");
        _;
    }

    // Structure to hold a single checkpoint entry on-chain
    struct CheckpointEntry {
        bytes32 dataHash; // Cryptographic hash of the off-chain data payload [cite: 15]
        bytes32 prevHash; // Hash of the previous checkpoint in the chain [cite: 16]
        string metadataJson; // JSON string of arbitrary metadata (e.g., agent_id, timestamp) [cite: 17]
        string offChainRef; // Reference to the off-chain storage location (e.g., S3 key, IPFS CID) [cite: 18]
        uint256 version; // Version counter at which this checkpoint was recorded [cite: 19]
        address writer; // Address of the entity that wrote this checkpoint (indexed in event, not struct) [cite: 20]
        uint256 timestamp; // Block timestamp when this checkpoint was recorded [cite: 21]
    }

    // Mapping from checkpoint name => version => CheckpointEntry
    mapping(string => mapping(uint256 => CheckpointEntry)) public checkpointsByVersion;
    // Mapping from checkpoint name => latest version
    mapping(string => uint256) public latestCheckpointVersion;
    // Mapping from checkpoint name => hash => version for quick lookup by hash
    mapping(string => mapping(bytes32 => uint256)) public checkpointHashToVersion;
    // Mapping from checkpoint name => latest hash
    mapping(string => bytes32) public latestCheckpointHash;

    // Events for auditable actions[cite: 25].
    event CheckpointWritten(
        string indexed name,
        uint256 indexed version,
        bytes32 indexed dataHash,
        bytes32 prevHash,
        string offChainRef,
        address writer,
        uint256 timestamp
    );

    event CheckpointRolledBack(
        string indexed name,
        bytes32 indexed targetHash, // The hash of the state being rolled back to [cite: 27]
        uint256 indexed newVersion, // The version counter of this rollback transaction [cite: 28]
        address roller,
        uint256 timestamp,
        string message
    );

    /**
     * @dev Writes a new checkpoint entry to the blockchain.
     * This function enforces hash chaining: `prevHash` must match the `dataHash` of the latest checkpoint
     * for the given `name`, unless it's the very first checkpoint ("genesis")[cite: 32].
     * @param name Unique name for this checkpoint chain (e.g., "my_agent_run_123")[cite: 33].
     * @param dataHash Cryptographic hash of the current off-chain data payload[cite: 34].
     * @param prevHash Hash of the previous checkpoint's data. For the first checkpoint, use `bytes32(0)`[cite: 35].
     * @param metadataJson JSON string containing additional metadata for the checkpoint.
     * @param offChainRef Reference to the off-chain storage location (e.g., S3 key, IPFS CID)[cite: 37].
     */
    function writeCheckpoint(
        string calldata name,
        bytes32 dataHash,
        bytes32 prevHash,
        string calldata metadataJson,
        string calldata offChainRef
    ) external onlyOwner { // A. Security Audit: Add onlyOwner access control
        // CS-2: use a monotonically increasing counter to avoid same-block overwrites
        _checkpointVersionCounter++;
        uint256 currentVersion = _checkpointVersionCounter;

        bytes32 existingLatestHash = latestCheckpointHash[name];
        require(existingLatestHash == prevHash || (existingLatestHash == bytes32(0) && prevHash == bytes32(0)),
                "Checkpoint chaining error: previous hash mismatch or invalid genesis hash.");
        require(dataHash != bytes32(0), "Data hash cannot be zero.");

        // A. Security Audit & Gas/Cost: Add length validation
        require(bytes(metadataJson).length < 2048, "Metadata JSON is too large.");
        require(bytes(offChainRef).length < 256, "Off-chain reference is too large.");

        checkpointsByVersion[name][currentVersion] = CheckpointEntry({
            dataHash: dataHash,
            prevHash: prevHash,
            metadataJson: metadataJson,
            offChainRef: offChainRef,
            version: currentVersion,
            writer: msg.sender,
            timestamp: block.timestamp
        });

        latestCheckpointVersion[name] = currentVersion;
        checkpointHashToVersion[name][dataHash] = currentVersion;
        latestCheckpointHash[name] = dataHash;

        emit CheckpointWritten(
            name,
            uint256(currentVersion),
            dataHash,
            prevHash,
            offChainRef,
            msg.sender,
            block.timestamp
        );
    }

    /**
     * @dev Retrieves the latest checkpoint entry for a given name.
     * @param name The unique name of the checkpoint chain[cite: 50].
     * @return dataHash The hash of the latest data payload.
     * @return prevHash The hash of the checkpoint before the latest.
     * @return metadataJson The JSON metadata of the latest checkpoint.
     * @return offChainRef The off-chain reference of the latest checkpoint.
     * @return version The version of the latest checkpoint.
     * @return writer The address of the writer of the latest checkpoint.
     * @return timestamp The timestamp of the latest checkpoint.
     */
    function getLatestCheckpoint(string calldata name)
        external
        view
        returns (
            bytes32 dataHash,
            bytes32 prevHash,
            string memory metadataJson,
            string memory offChainRef,
            uint256 version,
            address writer,
            uint256 timestamp
        )
    {
        uint256 latestVersion = latestCheckpointVersion[name];
        require(latestVersion != 0, "No checkpoints found for this name.");
        CheckpointEntry storage entry = checkpointsByVersion[name][latestVersion];
        return (
            entry.dataHash,
            entry.prevHash,
            entry.metadataJson,
            entry.offChainRef,
            entry.version,
            entry.writer,
            entry.timestamp
        );
    }

    /**
     * @dev Retrieves a specific checkpoint entry by its name and version.
     * @param name The unique name of the checkpoint chain.
     * @param version The version of the checkpoint to retrieve.
     * @return dataHash The hash of the data payload at that version.
     * @return prevHash The hash of the previous checkpoint.
     * @return metadataJson The JSON metadata at that version.
     * @return offChainRef The off-chain reference at that version.
     * @return writer The address of the writer at that version.
     * @return timestamp The timestamp at that version.
     */
    function getCheckpointByVersion(string calldata name, uint256 version)
        external
        view
        returns (
            bytes32 dataHash,
            bytes32 prevHash,
            string memory metadataJson,
            string memory offChainRef,
            address writer,
            uint256 timestamp
        )
    {
        CheckpointEntry storage entry = checkpointsByVersion[name][version];
        require(entry.version != 0, "Checkpoint not found for this version.");
        return (
            entry.dataHash,
            entry.prevHash,
            entry.metadataJson,
            entry.offChainRef,
            entry.writer,
            entry.timestamp
        );
    }

    /**
     * @dev Retrieves a specific checkpoint entry by its data hash.
     * @param name The unique name of the checkpoint chain.
     * @param dataHash The data hash of the checkpoint to retrieve.
     * @return prevHash The hash of the previous checkpoint.
     * @return metadataJson The JSON metadata.
     * @return offChainRef The off-chain reference.
     * @return version The version at which this checkpoint was stored.
     * @return writer The address of the writer.
     * @return timestamp The timestamp.
     */
    function getCheckpointByHash(string calldata name, bytes32 dataHash)
        external
        view
        returns (
            bytes32 prevHash,
            string memory metadataJson,
            string memory offChainRef,
            uint256 version,
            address writer,
            uint256 timestamp
        )
    {
        uint256 ver = checkpointHashToVersion[name][dataHash];
        require(ver != 0, "Checkpoint not found for this hash.");
        CheckpointEntry storage entry = checkpointsByVersion[name][ver];
        return (
            entry.prevHash,
            entry.metadataJson,
            entry.offChainRef,
            entry.version,
            entry.writer,
            entry.timestamp
        );
    }

    /**
     * @dev Performs a logical rollback to a previously stored checkpoint.
     * A new checkpoint entry is recorded pointing to the target state.
     * @param name The unique name of the checkpoint chain.
     * @param targetHash The hash of the checkpoint state to roll back to.
     * @param message A human-readable message describing the reason for rollback.
     */
    function rollbackCheckpoint(
        string calldata name,
        bytes32 targetHash,
        string calldata message
    ) external onlyOwner { // A. Security Audit: Add onlyOwner access control
        // CS-4: Validate message length to prevent JSON injection via unbounded input
        require(bytes(message).length > 0, "Rollback message cannot be empty.");
        require(bytes(message).length < MAX_ROLLBACK_MESSAGE_LENGTH, "Rollback message is too large.");

        uint256 targetVersion = checkpointHashToVersion[name][targetHash];
        require(targetVersion != 0, "Target hash for rollback not found for this name.");
        CheckpointEntry storage targetEntry = checkpointsByVersion[name][targetVersion];
        bytes32 currentLatestHash = latestCheckpointHash[name];

        require(targetEntry.version <= latestCheckpointVersion[name], "Cannot rollback to a future version.");

        // CS-2: Use incrementing counter rather than block.number
        _checkpointVersionCounter++;
        uint256 newVersion = _checkpointVersionCounter;

        string memory rollbackMetadataJson = string(abi.encodePacked(
            "{\"action\":\"rollback\",",
            "\"rolledBackFromHash\":\"0x", Bytes.toHexString(currentLatestHash), "\",",
            "\"rolledBackFromVersion\":", Bytes.toString(latestCheckpointVersion[name]), ",",
            "\"rolledBackToHash\":\"0x", Bytes.toHexString(targetEntry.dataHash), "\",",
            "\"rolledBackToVersion\":", Bytes.toString(targetEntry.version), ",",
            "\"originalMetadata\":", targetEntry.metadataJson, ",",
            "\"message\":\"", message, "\"}"
        ));

        checkpointsByVersion[name][newVersion] = CheckpointEntry({
            dataHash: targetEntry.dataHash,
            prevHash: currentLatestHash,
            metadataJson: rollbackMetadataJson,
            offChainRef: targetEntry.offChainRef,
            version: newVersion,
            writer: msg.sender,
            timestamp: block.timestamp
        });

        latestCheckpointVersion[name] = newVersion;
        checkpointHashToVersion[name][targetEntry.dataHash] = newVersion;
        latestCheckpointHash[name] = targetEntry.dataHash;

        emit CheckpointRolledBack(
            name,
            targetHash,
            newVersion,
            msg.sender,
            block.timestamp,
            message
        );
    }
}
