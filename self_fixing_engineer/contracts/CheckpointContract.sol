// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.21;

// The contract now relies on standard string and abi encoding functions.
// If you're using OpenZeppelin, the `Strings` library is included for `toString` and `toHexString`
// which can be useful for external libraries or off-chain tools.
// The internal `Strings` library below is for demonstration and self-containment only;
// a production contract should import and use battle-hardened, audited libraries.

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
 * -   **Access Control**: This contract includes an `onlyOwner` modifier for critical functions to restrict access to a trusted entity, which is mandatory for production SaaS[cite: 9].
 * -   **String Normalization**: Clients should normalize checkpoint names (e.g., lowercase) before sending to the contract for consistent lookups[cite: 11].
 * -   **Chain Growth**: Every state change (save, rollback) adds a new entry[cite: 12]. For very long-lived
 * checkpoint chains, consider off-chain indexing for historical lookups beyond the latest[cite: 13].
 *
 * @custom:testing-strategy
 * -   Test all functions with valid and invalid inputs.
 * -   Test edge cases: first checkpoint (genesis), rolling back to a hash that doesn't exist,
 * rolling back to the current latest hash, and rolling back to a hash that has been
 * written multiple times.
 * -   Test unauthorized access attempts on protected functions (`writeCheckpoint`, `rollbackCheckpoint`).
 *
 * @custom:deployment-strategy
 * -   Use a deployment script (e.g., Hardhat, Truffle) to deploy this contract.
 * -   For upgradability, use a proxy pattern (e.g., OpenZeppelin Upgrades) and document the upgrade path.
 */
contract CheckpointContract {
    // Defines a mapping from an address to an address.
    address private owner;

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
        uint256 version; // Block number at which this checkpoint was recorded [cite: 19]
        address writer; // Address of the entity that wrote this checkpoint (indexed in event, not struct) [cite: 20]
        uint256 timestamp; // Block timestamp when this checkpoint was recorded [cite: 21]
    }

    // Mapping from checkpoint name => version (block number) => CheckpointEntry
    mapping(string => mapping(uint256 => CheckpointEntry)) public checkpointsByVersion;
    // Mapping from checkpoint name => latest version (block number)
    mapping(string => uint256) public latestCheckpointVersion;
    // Mapping from checkpoint name => hash => version (block number) for quick lookup by hash
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
        uint256 indexed newVersion, // The block number of this rollback transaction [cite: 28]
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
        uint256 currentBlockNumber = block.number;

        bytes32 existingLatestHash = latestCheckpointHash[name];
        require(existingLatestHash == prevHash || (existingLatestHash == bytes32(0) && prevHash == bytes32(0)),
                "Checkpoint chaining error: previous hash mismatch or invalid genesis hash.");
        require(dataHash != bytes32(0), "Data hash cannot be zero.");

        // A. Security Audit & Gas/Cost: Add length validation
        require(bytes(metadataJson).length < 2048, "Metadata JSON is too large.");
        require(bytes(offChainRef).length < 256, "Off-chain reference is too large.");

        checkpointsByVersion[name][currentBlockNumber] = CheckpointEntry({
            dataHash: dataHash,
            prevHash: prevHash,
            metadataJson: metadataJson,
            offChainRef: offChainRef,
            version: currentBlockNumber,
            writer: msg.sender,
            timestamp: block.timestamp
        });

        latestCheckpointVersion[name] = currentBlockNumber;
        checkpointHashToVersion[name][dataHash] = currentBlockNumber;
        latestCheckpointHash[name] = dataHash;

        emit CheckpointWritten(
            name,
            uint256(currentBlockNumber),
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
     * @return version The block number of the latest checkpoint.
     * @return writer The address of the writer of the latest checkpoint.
     * @return timestamp The timestamp of the latest checkpoint.
     */
    function getLatestCheckpoint(string calldata name)
        external
        view
        returns (bytes32, bytes32, string memory, string memory, uint256, address, uint256)
    {
        uint256 latestVer = latestCheckpointVersion[name];
        require(latestVer != 0, "Checkpoint not found for this name or chain is empty.");
        CheckpointEntry storage entry = checkpointsByVersion[name][latestVer];
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
     * @dev Retrieves a specific checkpoint entry by its name and version (block number).
     * @param name The unique name of the checkpoint chain[cite: 56].
     * @param version The block number at which the checkpoint was recorded[cite: 57].
     * @return dataHash, prevHash, metadataJson, offChainRef, version, writer, timestamp
     */
    function readCheckpoint(string calldata name, uint256 version)
        external
        view
        returns (bytes32, bytes32, string memory, string memory, uint256, address, uint256)
    {
        require(checkpointsByVersion[name][version].version != 0, "Checkpoint version not found for this name.");
        CheckpointEntry storage entry = checkpointsByVersion[name][version];
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
     * @dev Retrieves a specific checkpoint entry by its dataHash.
     * This is useful for verifying hashes directly or finding older states[cite: 61].
     * Note: If the same hash is written multiple times (e.g., via rollbacks),
     * this function returns the details of the *latest* entry associated with that hash[cite: 63].
     * @param name The unique name of the checkpoint chain[cite: 64].
     * @param dataHash The cryptographic hash of the data for the desired checkpoint[cite: 66].
     * @return dataHash, prevHash, metadataJson, offChainRef, version, writer, timestamp
     */
    function getCheckpointByHash(string calldata name, bytes32 dataHash)
        external
        view
        returns (bytes32, bytes32, string memory, string memory, uint256, address, uint256)
    {
        uint256 version = checkpointHashToVersion[name][dataHash];
        require(version != 0, "Checkpoint with this hash not found for this name.");
        
        CheckpointEntry storage entry = checkpointsByVersion[name][version];
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
     * @dev Performs a "logical" rollback by creating a new checkpoint entry
     * that points to an *older*, previously committed data hash.
     * This maintains an unbroken chain of records on the DLT,
     * while establishing a new "latest" state that effectively rolls back[cite: 71].
     * @param name The unique name of the checkpoint chain[cite: 73].
     * @param targetHash The dataHash of the checkpoint entry to logically roll back to[cite: 74].
     * @param message A message describing the rollback reason.
     */
    function rollbackCheckpoint(
        string calldata name,
        bytes32 targetHash,
        string calldata message
    ) external onlyOwner { // A. Security Audit: Add onlyOwner access control
        uint256 targetVersion = checkpointHashToVersion[name][targetHash];
        require(targetVersion != 0, "Target hash for rollback not found for this name.");
        CheckpointEntry storage targetEntry = checkpointsByVersion[name][targetVersion];
        bytes32 currentLatestHash = latestCheckpointHash[name];
        uint256 newVersion = block.number;
        
        require(targetEntry.version <= latestCheckpointVersion[name], "Cannot rollback to a future version.");
        
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

    /**
     * @dev Library for safe bytes to string conversions.
     * This library is self-contained for the demo, but in a production environment,
     * it's highly recommended to use an audited library like OpenZeppelin's `Strings.sol`.
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
}