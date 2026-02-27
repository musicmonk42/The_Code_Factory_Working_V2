// Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"regexp"
	"strconv"
	"strings"

	"github.com/hyperledger/fabric-chaincode-go/pkg/cid"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"
	"github.com/hyperledger/fabric-protos-go/msp"
	// fabric-protos-go v0.3.x generates legacy proto2 types (XXX_ fields) that satisfy
	// the github.com/golang/protobuf interface, not the newer google.golang.org/protobuf
	// interface. Using the legacy package is intentional and required for compatibility.
	"github.com/golang/protobuf/proto"
)

// chaincodeLogger is a simple logger wrapper that supports leveled logging.
type chaincodeLogger struct {
	inner    *log.Logger
	minLevel int
}

const (
	levelDebug   = 0
	levelInfo    = 1
	levelWarning = 2
	levelError   = 3
)

func newChaincodeLogger(name string) *chaincodeLogger {
	return &chaincodeLogger{
		inner:    log.New(os.Stderr, "["+name+"] ", log.LstdFlags),
		minLevel: levelInfo,
	}
}

func (l *chaincodeLogger) SetLevel(level int) { l.minLevel = level }
func (l *chaincodeLogger) GetLevel() int      { return l.minLevel }

func (l *chaincodeLogger) Debugf(format string, args ...interface{}) {
	if l.minLevel <= levelDebug {
		l.inner.Printf("DEBUG "+format, args...)
	}
}
func (l *chaincodeLogger) Infof(format string, args ...interface{}) {
	if l.minLevel <= levelInfo {
		l.inner.Printf("INFO "+format, args...)
	}
}
func (l *chaincodeLogger) Info(args ...interface{}) {
	if l.minLevel <= levelInfo {
		l.inner.Printf("INFO %s", fmt.Sprint(args...))
	}
}
func (l *chaincodeLogger) Warningf(format string, args ...interface{}) {
	if l.minLevel <= levelWarning {
		l.inner.Printf("WARN "+format, args...)
	}
}
func (l *chaincodeLogger) Errorf(format string, args ...interface{}) {
	if l.minLevel <= levelError {
		l.inner.Printf("ERROR "+format, args...)
	}
}

// Define logger for the chaincode
var logger = newChaincodeLogger("CheckpointChaincode")

// InitLogger configures the logger level based on an environment variable.
// Rationale: Allows dynamic tuning of log verbosity without recompiling the chaincode.
func InitLogger() {
	logLevel := os.Getenv("CORE_CHAINCODE_LOGGING_SHIM")
	switch logLevel {
	case "INFO":
		logger.SetLevel(levelInfo)
	case "DEBUG":
		logger.SetLevel(levelDebug)
	case "WARNING":
		logger.SetLevel(levelWarning)
	case "ERROR":
		logger.SetLevel(levelError)
	default:
		logger.SetLevel(levelInfo) // Default to INFO
	}
	logger.Info("Logger initialized with level: ", logger.GetLevel())
}

func init() {
	InitLogger()
}

// SmartContract defines the smart contract structure.
// This struct embeds contractapi.Contract, providing access to useful functions.
type SmartContract struct {
	contractapi.Contract
}

// CheckpointEntry structure for storing data on the ledger.
// Each field is tagged for JSON serialization.
type CheckpointEntry struct {
	Name         string `json:"name"`         // Unique name for this checkpoint chain (e.g., "my_agent_run_123")
	DataHash     string `json:"dataHash"`     // Cryptographic hash of the current off-chain data payload
	PrevHash     string `json:"prevHash"`     // Hash of the previous checkpoint's data in the chain
	MetadataJson string `json:"metadataJson"` // JSON string of arbitrary metadata (e.g., agent_id, timestamp). Sanitized before storage.
	OffChainRef  string `json:"offChainRef"`  // Reference to the off-chain storage location (e.g., S3 key, IPFS CID). Sanitized before storage.
	TxID         string `json:"txId"`         // Transaction ID that created this checkpoint
	Timestamp    int64  `json:"timestamp"`    // Unix timestamp in milliseconds when this checkpoint was recorded
	Version      int    `json:"version"`      // Sequential version for this checkpoint chain (managed by chaincode)
	Writer       string `json:"writer"`       // ID of the writer (MSP ID or client ID)
	IsRollback   bool   `json:"isRollback"`   // Indicates if this entry is a rollback operation
}

// CheckpointState tracks the latest version and hash for each named checkpoint chain.
// This is stored separately to allow quick lookup of the current head of a chain.
type CheckpointState struct {
	LatestVersion int    `json:"latestVersion"`
	LatestHash    string `json:"latestHash"`
}

// Input validation regex patterns.
// Use constants for magic numbers to improve readability and maintainability.
const (
	MaxNameLen        = 100
	SHA256HashLen     = 64
	MaxOffChainRefLen = 256
	MaxMessageLen     = 500
)

var (
	nameRegex = regexp.MustCompile(fmt.Sprintf("^[a-zA-Z0-9_-]{1,%d}$", MaxNameLen))
	hashRegex = regexp.MustCompile(fmt.Sprintf("^[a-f0-9]{%d}$", SHA256HashLen))
	// offChainRegex validates path segment characters; total length is enforced separately.
	offChainRegex = regexp.MustCompile(`^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)*$`)
)

// InitLedger adds a base set of assets to the ledger during chaincode instantiation.
// It's typically used for initial setup or migrations.
func (s *SmartContract) InitLedger(ctx contractapi.TransactionContextInterface) error {
	logger.Info("Initializing DLT Checkpoint Chaincode Ledger")
	// No initial checkpoints needed, ledger starts empty.
	// This function can be extended for future schema migrations or initial data seeding.
	return nil
}

// HealthCheck provides a simple read-only function for health monitoring.
// It can be invoked by external systems to verify chaincode responsiveness.
func (s *SmartContract) HealthCheck(ctx contractapi.TransactionContextInterface) (string, error) {
	logger.Info("HealthCheck invoked")
	// In a more complex scenario, this could check connectivity to external services
	// or internal state.
	return "Chaincode is healthy", nil
}

// contains checks if a string is present in a slice of strings.
func contains(s []string, str string) bool {
	for _, v := range s {
		if v == str {
			return true
		}
	}
	return false
}

// hasRole checks if the transaction invoker has the required role based on their MSP ID.
// Rationale: This implements a basic form of RBAC, essential for production security.
func (s *SmartContract) hasRole(ctx contractapi.TransactionContextInterface, requiredRoles ...string) (bool, error) {
	clientCID, err := cid.New(ctx.GetStub())
	if err != nil {
		return false, fmt.Errorf("failed to get client identity: %w", err)
	}

	mspID, err := clientCID.GetMSPID()
	if err != nil {
		return false, fmt.Errorf("failed to get MSP ID from client identity: %w", err)
	}
	logger.Debugf("Transaction invoked by MSP ID: %s", mspID)

	// In a real-world scenario, roles would be checked here. For this implementation,
	// we'll assume a basic policy based on MSP ID.
	if contains(requiredRoles, mspID) {
		return true, nil
	}

	// This is a simplified check. A production system would use attributes.
	// For example: `val, ok, err := clientCID.GetAttributeValue("role")`

	return false, nil
}

// WriteCheckpoint creates a new checkpoint entry on the ledger.
// It enforces hash chaining and creates a secondary index for dataHash lookup.
//
// Parameters:
//   ctx: The transaction context.
//   name: Unique identifier for the checkpoint chain.
//   dataHash: Cryptographic hash of the current off-chain data payload.
//   prevHash: Hash of the previous checkpoint's data in the chain.
//   metadataJson: JSON string of arbitrary metadata.
//   offChainRef: Reference to the off-chain storage location.
//
// Returns:
//   *CheckpointEntry: The newly created checkpoint entry.
//   error: An error if the operation fails due to validation, chaining, or ledger interaction.
//
// Security: Never accept unvalidated offChainRef or metadataJson; sanitize before storage.
// Validation: All incoming parameters (name, hash, metadata) for type, length, content.
// Chain Integrity: Previous hash checks must never be bypassable, even by admin.
func (s *SmartContract) WriteCheckpoint(ctx contractapi.TransactionContextInterface, name string, dataHash string, prevHash string, metadataJson string, offChainRef string) (*CheckpointEntry, error) {
	logger.Infof("Writing checkpoint for name: %s, dataHash: %s", name, dataHash)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "admin"); err != nil {
		return nil, fmt.Errorf("failed to check authorization: %w", err)
	} else if !ok {
		return nil, fmt.Errorf("unauthorized: caller does not have the required 'admin' role")
	}

	// 2. Input Validation
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s. Must be alphanumeric, hyphen, underscore (1-%d chars)", name, MaxNameLen)
	}
	if !hashRegex.MatchString(dataHash) {
		return nil, fmt.Errorf("invalid data hash: %s. Must be a %d-character SHA256 hex string", dataHash, SHA256HashLen)
	}
	if prevHash != "" && !hashRegex.MatchString(prevHash) {
		return nil, fmt.Errorf("invalid previous hash: %s. Must be a %d-character SHA256 hex string or empty", prevHash, SHA256HashLen)
	}
	if offChainRef != "" {
		if len(offChainRef) > MaxOffChainRefLen {
			return nil, fmt.Errorf("invalid off-chain reference: exceeds maximum length of %d chars", MaxOffChainRefLen)
		}
		if !offChainRegex.MatchString(offChainRef) {
			return nil, fmt.Errorf("invalid off-chain reference: %s. Contains disallowed characters", offChainRef)
		}
		if strings.Contains(offChainRef, "..") {
			return nil, fmt.Errorf("invalid off-chain reference: path traversal sequences not allowed")
		}
	}
	// Basic JSON validation for metadataJson
	if metadataJson != "" && !json.Valid([]byte(metadataJson)) {
		return nil, fmt.Errorf("invalid metadataJson: not a valid JSON string")
	}

	// 3. Get current checkpoint state for chaining
	var checkpointStateJSON []byte
	checkpointStateJSON, err := ctx.GetStub().GetState(name)
	if err != nil {
		logger.Errorf("Failed to read checkpoint state for %s from world state: %v", name, err)
		return nil, fmt.Errorf("failed to read checkpoint state: %v", err)
	}

	currentCheckpointState := CheckpointState{}
	if checkpointStateJSON != nil {
		err = json.Unmarshal(checkpointStateJSON, &currentCheckpointState)
		if err != nil {
			logger.Errorf("Failed to unmarshal checkpoint state JSON for %s: %v", name, err)
			return nil, fmt.Errorf("failed to process checkpoint state: %v", err)
		}
	}

	// 4. Enforce hash chaining: prevHash must match the current latest hash, or be empty for genesis
	// Chain Integrity: Previous hash checks must never be bypassable.
	if currentCheckpointState.LatestHash != "" && currentCheckpointState.LatestHash != prevHash {
		logger.Errorf("Checkpoint chaining error for %s: previous hash mismatch. Expected %s, got %s", name, currentCheckpointState.LatestHash, prevHash)
		return nil, fmt.Errorf("checkpoint chaining error: previous hash mismatch")
	}
	if currentCheckpointState.LatestHash == "" && prevHash != "" {
		logger.Errorf("Checkpoint chaining error for %s: first checkpoint must have an empty previous hash, got %s", name, prevHash)
		return nil, fmt.Errorf("checkpoint chaining error: first checkpoint must have an empty previous hash")
	}

	// 5. Get client identity for writer field
	creatorBytes, err := ctx.GetStub().GetCreator()
	if err != nil {
		logger.Errorf("Failed to get transaction creator for %s: %v", name, err)
		return nil, fmt.Errorf("failed to get transaction creator identity")
	}
	serializedID := &msp.SerializedIdentity{}
	if err = proto.Unmarshal(creatorBytes, serializedID); err != nil {
		logger.Errorf("Failed to deserialize creator identity for %s: %v", name, err)
		return nil, fmt.Errorf("failed to deserialize creator identity: %w", err)
	}
	writerID := serializedID.Mspid

	// 6. Determine new version
	newVersion := currentCheckpointState.LatestVersion + 1

	// 7. Get transaction timestamp (deterministic across endorsing peers)
	txTimestamp, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		logger.Errorf("Failed to get transaction timestamp for %s: %v", name, err)
		return nil, fmt.Errorf("failed to get transaction timestamp: %w", err)
	}
	timestamp := txTimestamp.GetSeconds()*1000 + int64(txTimestamp.GetNanos()/1_000_000)

	// 8. Create new checkpoint entry
	checkpoint := CheckpointEntry{
		Name:         name,
		DataHash:     dataHash,
		PrevHash:     prevHash,
		MetadataJson: metadataJson, // Already validated as JSON string
		OffChainRef:  offChainRef,  // Already validated
		TxID:         ctx.GetStub().GetTxID(),
		Timestamp:    timestamp,
		Version:      newVersion,
		Writer:       writerID,
		IsRollback:   false, // This is a regular write
	}

	// 9. Marshal and store checkpoint entry to ledger (Primary Key)
	checkpointJSON, err := json.Marshal(checkpoint)
	if err != nil {
		logger.Errorf("Failed to marshal checkpoint object for %s: %v", name, err)
		return nil, fmt.Errorf("failed to prepare checkpoint data")
	}

	primaryCompositeKey, err := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%010d", newVersion)})
	if err != nil {
		logger.Errorf("Failed to create primary composite key for %s v%d: %v", name, newVersion, err)
		return nil, fmt.Errorf("failed to create ledger key")
	}
	err = ctx.GetStub().PutState(primaryCompositeKey, checkpointJSON)
	if err != nil {
		logger.Errorf("Failed to put checkpoint %s v%d to world state: %v", name, newVersion, err)
		return nil, fmt.Errorf("failed to store checkpoint")
	}

	// 10. Create Secondary Index for DataHash lookup: "DataHashIndex"~name~dataHash -> primaryCompositeKey
	dataHashIndexKey, err := ctx.GetStub().CreateCompositeKey("DataHashIndex", []string{name, dataHash})
	if err != nil {
		logger.Errorf("Failed to create dataHash index composite key for %s hash %s: %v", name, dataHash, err)
		return nil, fmt.Errorf("failed to create index key")
	}
	err = ctx.GetStub().PutState(dataHashIndexKey, []byte(primaryCompositeKey))
	if err != nil {
		logger.Errorf("Failed to put dataHash index for %s hash %s to world state: %v", name, dataHash, err)
		return nil, fmt.Errorf("failed to store index")
	}

	// 11. Update checkpoint state (latest version and hash)
	currentCheckpointState.LatestVersion = newVersion
	currentCheckpointState.LatestHash = dataHash
	checkpointStateJSON, err = json.Marshal(currentCheckpointState)
	if err != nil {
		logger.Errorf("Failed to marshal updated checkpoint state for %s: %v", name, err)
		return nil, fmt.Errorf("failed to update latest state")
	}
	err = ctx.GetStub().PutState(name, checkpointStateJSON) // Store latest state by name
	if err != nil {
		logger.Errorf("Failed to update latest checkpoint state for %s: %v", name, err)
		return nil, fmt.Errorf("failed to update latest state")
	}

	logger.Infof("Checkpoint %s version %d written with TxID %s. Secondary index for hash %s created.", name, newVersion, ctx.GetStub().GetTxID(), dataHash)
	return &checkpoint, nil
}

// ReadCheckpoint retrieves a checkpoint entry by name and optionally version.
// If version is empty string, it returns the latest checkpoint.
//
// Parameters:
//   ctx: The transaction context.
//   name: The unique name of the checkpoint chain.
//   version: The specific version to retrieve. If empty string, the latest is returned.
//
// Returns:
//   *CheckpointEntry: The retrieved checkpoint entry.
//   error: An error if the checkpoint is not found or retrieval fails.
func (s *SmartContract) ReadCheckpoint(ctx contractapi.TransactionContextInterface, name string, version string) (*CheckpointEntry, error) {
	logger.Infof("Reading checkpoint for name: %s, version: %v", name, version)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "reader"); err != nil {
		return nil, fmt.Errorf("failed to check authorization: %w", err)
	} else if !ok {
		return nil, fmt.Errorf("unauthorized: caller does not have the required 'reader' role")
	}

	// 2. Input Validation for name
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s", name)
	}
	if version != "" {
		// Basic version string validation (should be a positive integer)
		if _, err := fmt.Sscanf(version, "%d", new(int)); err != nil {
			return nil, fmt.Errorf("invalid version format: %s. Must be an integer", version)
		}
	}

	var checkpointJSON []byte
	var err error

	if version != "" {
		// Parse and zero-pad for consistent key format; handle leading-zero inputs gracefully
		trimmed := strings.TrimLeft(version, "0")
		if trimmed == "" {
			trimmed = "0"
		}
		versionInt, parseErr := strconv.Atoi(trimmed)
		if parseErr != nil {
			return nil, fmt.Errorf("invalid version format: %s. Must be a non-negative integer", version)
		}
		compositeKey, keyErr := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%010d", versionInt)})
		if keyErr != nil {
			logger.Errorf("Failed to create composite key for %s v%s: %v", name, version, keyErr)
			return nil, fmt.Errorf("failed to create ledger key")
		}
		checkpointJSON, err = ctx.GetStub().GetState(compositeKey)
		if err != nil {
			logger.Errorf("Failed to read specific checkpoint %s v%s from world state: %v", name, version, err)
			return nil, fmt.Errorf("failed to retrieve checkpoint")
		}
	} else {
		// Read latest version
		checkpointStateJSON, stateErr := ctx.GetStub().GetState(name)
		if stateErr != nil {
			logger.Errorf("Failed to read latest checkpoint state for %s from world state: %v", name, stateErr)
			return nil, fmt.Errorf("failed to retrieve latest state")
		}
		if checkpointStateJSON == nil {
			logger.Warningf("No checkpoint state found for name %s", name)
			return nil, fmt.Errorf("no checkpoint state found for name %s", name)
		}

		checkpointState := CheckpointState{}
		unmarshalErr := json.Unmarshal(checkpointStateJSON, &checkpointState)
		if unmarshalErr != nil {
			logger.Errorf("Failed to unmarshal latest checkpoint state for %s: %v", name, unmarshalErr)
			return nil, fmt.Errorf("failed to process latest state")
		}

		compositeKey, keyErr := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%010d", checkpointState.LatestVersion)})
		if keyErr != nil {
			logger.Errorf("Failed to create composite key for latest version %s v%d: %v", name, checkpointState.LatestVersion, keyErr)
			return nil, fmt.Errorf("failed to create ledger key for latest version")
		}
		checkpointJSON, err = ctx.GetStub().GetState(compositeKey)
		if err != nil {
			logger.Errorf("Failed to read latest checkpoint %s v%d from world state: %v", name, checkpointState.LatestVersion, err)
			return nil, fmt.Errorf("failed to retrieve latest checkpoint")
		}
	}

	if checkpointJSON == nil {
		logger.Warningf("Checkpoint %s (version %s) does not exist", name, version)
		return nil, fmt.Errorf("checkpoint %s (version %s) does not exist", name, version)
	}

	checkpoint := CheckpointEntry{}
	err = json.Unmarshal(checkpointJSON, &checkpoint)
	if err != nil {
		logger.Errorf("Failed to unmarshal checkpoint JSON for %s v%s: %v", name, version, err)
		return nil, fmt.Errorf("failed to process checkpoint data")
	}

	logger.Infof("Checkpoint %s version %d read successfully", name, checkpoint.Version)
	return &checkpoint, nil
}

// ReadCheckpointByHash retrieves a checkpoint entry by name and dataHash using the secondary index.
// This function is useful for looking up a specific state by its content hash.
//
// Parameters:
//   ctx: The transaction context.
//   name: The unique name of the checkpoint chain.
//   dataHash: The cryptographic hash of the off-chain data payload.
//
// Returns:
//   *CheckpointEntry: The retrieved checkpoint entry.
//   error: An error if the checkpoint is not found or retrieval fails.
func (s *SmartContract) ReadCheckpointByHash(ctx contractapi.TransactionContextInterface, name string, dataHash string) (*CheckpointEntry, error) {
	logger.Infof("Reading checkpoint for name: %s, dataHash: %s using index", name, dataHash)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "reader"); err != nil {
		return nil, fmt.Errorf("failed to check authorization: %w", err)
	} else if !ok {
		return nil, fmt.Errorf("unauthorized: caller does not have the required 'reader' role")
	}

	// 2. Input Validation
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s", name)
	}
	if !hashRegex.MatchString(dataHash) {
		return nil, fmt.Errorf("invalid data hash: %s", dataHash)
	}

	// Use the secondary index to find the primary composite key
	dataHashIndexKey, err := ctx.GetStub().CreateCompositeKey("DataHashIndex", []string{name, dataHash})
	if err != nil {
		logger.Errorf("Failed to create dataHash index composite key for %s hash %s: %v", name, dataHash, err)
		return nil, fmt.Errorf("failed to create index key")
	}

	primaryCompositeKeyBytes, err := ctx.GetStub().GetState(dataHashIndexKey)
	if err != nil {
		logger.Errorf("Failed to read dataHash index for %s hash %s from world state: %v", name, dataHash, err)
		return nil, fmt.Errorf("failed to retrieve index entry")
	}
	if primaryCompositeKeyBytes == nil {
		logger.Warningf("Checkpoint with name %s and dataHash %s not found via index", name, dataHash)
		return nil, fmt.Errorf("checkpoint with name %s and dataHash %s not found", name, dataHash)
	}

	primaryCompositeKey := string(primaryCompositeKeyBytes)

	// Now use the retrieved primary composite key to get the actual CheckpointEntry
	checkpointJSON, err := ctx.GetStub().GetState(primaryCompositeKey)
	if err != nil {
		logger.Errorf("Failed to read checkpoint using primary key %s from world state: %v", primaryCompositeKey, err)
		return nil, fmt.Errorf("failed to retrieve checkpoint by index")
	}
	if checkpointJSON == nil {
		logger.Warningf("Checkpoint with primary key %s not found (index might be stale)", primaryCompositeKey)
		return nil, fmt.Errorf("checkpoint with primary key %s not found (index might be stale)", primaryCompositeKey)
	}

	checkpoint := CheckpointEntry{}
	err = json.Unmarshal(checkpointJSON, &checkpoint)
	if err != nil {
		logger.Errorf("Failed to unmarshal checkpoint JSON from primary key %s: %v", primaryCompositeKey, err)
		return nil, fmt.Errorf("failed to process checkpoint data from index")
	}

	logger.Infof("Checkpoint %s version %d (hash %s) read successfully via index", name, checkpoint.Version, dataHash)
	return &checkpoint, nil
}

// ReadCheckpointHistory retrieves the history of all checkpoints for a given name.
// This function is useful for audit trails and verifying the entire chain.
// Rationale: Pagination is added to prevent timeouts and resource exhaustion on large ledgers.
func (s *SmartContract) ReadCheckpointHistory(ctx contractapi.TransactionContextInterface, name string, startVersion int, endVersion int) ([]*CheckpointEntry, error) {
	logger.Infof("Reading history for checkpoint: %s from version %d to %d", name, startVersion, endVersion)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "auditor"); err != nil {
		return nil, fmt.Errorf("failed to check authorization: %w", err)
	} else if !ok {
		return nil, fmt.Errorf("unauthorized: caller does not have the required 'auditor' role")
	}

	// 2. Input Validation
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s", name)
	}
	if startVersion < 0 || endVersion < startVersion {
		return nil, fmt.Errorf("invalid version range: start version must be non-negative and less than or equal to end version")
	}

	// Use GetStateByRange with zero-padded keys for proper lexicographic ordering and pagination
	startKey, err := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%010d", startVersion)})
	if err != nil {
		logger.Errorf("Failed to create start composite key for history of %s: %v", name, err)
		return nil, fmt.Errorf("failed to create start key")
	}
	endKey, err := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%010d", endVersion+1)})
	if err != nil {
		logger.Errorf("Failed to create end composite key for history of %s: %v", name, err)
		return nil, fmt.Errorf("failed to create end key")
	}
	resultsIterator, err := ctx.GetStub().GetStateByRange(startKey, endKey)
	if err != nil {
		logger.Errorf("Failed to get history iterator for %s: %v", name, err)
		return nil, fmt.Errorf("failed to retrieve history iterator")
	}
	defer resultsIterator.Close()

	var entries []*CheckpointEntry
	for resultsIterator.HasNext() {
		queryResponse, iterErr := resultsIterator.Next()
		if iterErr != nil {
			logger.Errorf("Error during history iteration for %s: %v", name, iterErr)
			return nil, fmt.Errorf("error during history iteration")
		}

		var entry CheckpointEntry
		if unmarshalErr := json.Unmarshal(queryResponse.Value, &entry); unmarshalErr != nil {
			logger.Errorf("Failed to unmarshal history entry JSON for %s: %v", name, unmarshalErr)
			return nil, fmt.Errorf("failed to process history entry")
		}
		entries = append(entries, &entry)
	}
	
	logger.Infof("History for checkpoint %s retrieved successfully, found %d entries in range", name, len(entries))
	return entries, nil
}

// readCheckpointByHashInternal retrieves a checkpoint by hash without RBAC check.
// Used internally by RollbackCheckpoint to avoid requiring "reader" role for admins.
func (s *SmartContract) readCheckpointByHashInternal(ctx contractapi.TransactionContextInterface, name string, dataHash string) (*CheckpointEntry, error) {
	dataHashIndexKey, err := ctx.GetStub().CreateCompositeKey("DataHashIndex", []string{name, dataHash})
	if err != nil {
		return nil, fmt.Errorf("failed to create index key: %w", err)
	}
	primaryCompositeKeyBytes, err := ctx.GetStub().GetState(dataHashIndexKey)
	if err != nil {
		return nil, fmt.Errorf("failed to retrieve index entry: %w", err)
	}
	if primaryCompositeKeyBytes == nil {
		return nil, fmt.Errorf("checkpoint with name %s and dataHash %s not found", name, dataHash)
	}
	checkpointJSON, err := ctx.GetStub().GetState(string(primaryCompositeKeyBytes))
	if err != nil {
		return nil, fmt.Errorf("failed to retrieve checkpoint by index: %w", err)
	}
	if checkpointJSON == nil {
		return nil, fmt.Errorf("checkpoint with primary key %s not found (index might be stale)", string(primaryCompositeKeyBytes))
	}
	checkpoint := CheckpointEntry{}
	if err = json.Unmarshal(checkpointJSON, &checkpoint); err != nil {
		return nil, fmt.Errorf("failed to process checkpoint data from index: %w", err)
	}
	return &checkpoint, nil
}

// RollbackCheckpoint performs a logical rollback by writing a new checkpoint entry
// that points to an older, previously committed data hash. This operation is auditable
// and non-destructive, as it adds a new entry rather than modifying or deleting old ones.
//
// Parameters:
//   ctx: The transaction context.
//   name: The unique name of the checkpoint chain.
//   targetHash: The data hash of the checkpoint to roll back to.
//   message: A message explaining the reason for the rollback.
//
// Returns:
//   *CheckpointEntry: The newly created rollback checkpoint entry.
//   error: An error if the rollback fails due to validation, target not found, or ledger interaction.
//
// Rollbacks: Rollbacks must be auditable and non-destructive.
func (s *SmartContract) RollbackCheckpoint(ctx contractapi.TransactionContextInterface, name string, targetHash string, message string) (*CheckpointEntry, error) {
	logger.Infof("Rolling back checkpoint %s to targetHash: %s", name, targetHash)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "admin"); err != nil {
		return nil, fmt.Errorf("failed to check authorization: %w", err)
	} else if !ok {
		return nil, fmt.Errorf("unauthorized: caller does not have the required 'admin' role")
	}

	// 2. Input Validation
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s", name)
	}
	if !hashRegex.MatchString(targetHash) {
		return nil, fmt.Errorf("invalid target hash: %s", targetHash)
	}
	if strings.TrimSpace(message) == "" {
		return nil, fmt.Errorf("rollback message cannot be empty")
	}
	// Sanitize message to prevent injection or excessive length
	if len(message) > MaxMessageLen {
		message = message[:MaxMessageLen] + "..."
	}

	// 3. Find the target CheckpointEntry by targetHash using the internal helper (skips RBAC)
	targetEntry, err := s.readCheckpointByHashInternal(ctx, name, targetHash)
	if err != nil {
		logger.Errorf("Failed to find target checkpoint for rollback %s hash %s: %v", name, targetHash, err)
		return nil, fmt.Errorf("failed to find target checkpoint for rollback: %v", err)
	}
	if targetEntry == nil {
		logger.Warningf("Target checkpoint with hash %s not found for rollback of %s", targetHash, name)
		return nil, fmt.Errorf("target checkpoint with hash %s not found for rollback", targetHash)
	}

	// 4. Get current checkpoint state for chaining
	checkpointStateJSON, err := ctx.GetStub().GetState(name)
	if err != nil {
		logger.Errorf("Failed to read checkpoint state for %s from world state: %v", name, err)
		return nil, fmt.Errorf("failed to read checkpoint state: %v", err)
	}
	currentCheckpointState := CheckpointState{}
	if checkpointStateJSON != nil {
		err = json.Unmarshal(checkpointStateJSON, &currentCheckpointState)
		if err != nil {
			logger.Errorf("Failed to unmarshal checkpoint state JSON for %s: %v", name, err)
			return nil, fmt.Errorf("failed to process checkpoint state: %v", err)
		}
	}

	// 5. Prevent rolling back to itself if it's already the latest
	if currentCheckpointState.LatestHash == targetHash {
		logger.Warningf("Rollback to current latest hash %s for checkpoint %s. No effective change.", targetHash, name)
		return targetEntry, nil // Return the current entry if rolling back to itself
	}

	// 6. Create a new checkpoint entry that effectively "rolls back"
	newVersion := currentCheckpointState.LatestVersion + 1
	creatorBytes, err := ctx.GetStub().GetCreator()
	if err != nil {
		logger.Errorf("Failed to get transaction creator for rollback %s: %v", name, err)
		return nil, fmt.Errorf("failed to get transaction creator identity")
	}
	rollbackSerializedID := &msp.SerializedIdentity{}
	if err = proto.Unmarshal(creatorBytes, rollbackSerializedID); err != nil {
		logger.Errorf("Failed to deserialize creator identity for rollback %s: %v", name, err)
		return nil, fmt.Errorf("failed to deserialize creator identity: %w", err)
	}
	writerID := rollbackSerializedID.Mspid

	// Get transaction timestamp (deterministic across endorsing peers)
	txTimestamp, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		logger.Errorf("Failed to get transaction timestamp for rollback %s: %v", name, err)
		return nil, fmt.Errorf("failed to get transaction timestamp: %w", err)
	}
	timestamp := txTimestamp.GetSeconds()*1000 + int64(txTimestamp.GetNanos()/1_000_000)

	// Prepare metadata for the rollback entry (auditable)
	rollbackMetadata := map[string]interface{}{
		"rolledBackFromHash":    currentCheckpointState.LatestHash,
		"rolledBackFromVersion": currentCheckpointState.LatestVersion,
		"rolledBackToHash":      targetEntry.DataHash,
		"rolledBackToVersion":   targetEntry.Version,
		"originalMetadata":      json.RawMessage(targetEntry.MetadataJson), // Store original metadata
		"message":               message,
		"rollbackTxId":          ctx.GetStub().GetTxID(), // TxID of this rollback operation
		"rollbackTimestamp":     timestamp,
	}
	rollbackMetadataJson, err := json.Marshal(rollbackMetadata)
	if err != nil {
		logger.Errorf("Failed to marshal rollback metadata for %s: %v", name, err)
		return nil, fmt.Errorf("failed to prepare rollback metadata")
	}

	rollbackCheckpoint := CheckpointEntry{
		Name:         name,
		DataHash:     targetEntry.DataHash,              // The data hash we are rolling back to
		PrevHash:     currentCheckpointState.LatestHash, // Previous hash is the one we are rolling back from
		MetadataJson: string(rollbackMetadataJson),      // Enhanced metadata for auditability
		OffChainRef:  targetEntry.OffChainRef,           // Reference the off-chain payload of the target
		TxID:         ctx.GetStub().GetTxID(),
		Timestamp:    timestamp,
		Version:      newVersion,
		Writer:       writerID,
		IsRollback:   true, // Mark this entry as a rollback operation
	}

	rollbackJSON, err := json.Marshal(rollbackCheckpoint)
	if err != nil {
		logger.Errorf("Failed to marshal rollback checkpoint for %s: %v", name, err)
		return nil, fmt.Errorf("failed to prepare rollback checkpoint data")
	}

	// 7. Store rollback checkpoint by its new version (Primary Key)
	primaryCompositeKey, err := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%010d", newVersion)})
	if err != nil {
		logger.Errorf("Failed to create primary composite key for rollback %s v%d: %v", name, newVersion, err)
		return nil, fmt.Errorf("failed to create ledger key for rollback")
	}
	err = ctx.GetStub().PutState(primaryCompositeKey, rollbackJSON)
	if err != nil {
		logger.Errorf("Failed to put rollback checkpoint %s v%d to world state: %v", name, newVersion, err)
		return nil, fmt.Errorf("failed to store rollback checkpoint")
	}

	// 8. Update secondary index only if the hash doesn't already have an entry
	// Avoids overwriting the original entry's index, which would cause data loss
	dataHashIndexKey, err := ctx.GetStub().CreateCompositeKey("DataHashIndex", []string{name, targetEntry.DataHash})
	if err != nil {
		logger.Errorf("Failed to create dataHash index composite key for rollback %s hash %s: %v", name, targetEntry.DataHash, err)
		return nil, fmt.Errorf("failed to create index key for rollback")
	}
	existingIndex, err := ctx.GetStub().GetState(dataHashIndexKey)
	if err != nil {
		logger.Errorf("Failed to check existing dataHash index for rollback %s hash %s: %v", name, targetEntry.DataHash, err)
		return nil, fmt.Errorf("failed to check existing index for rollback")
	}
	if existingIndex == nil {
		err = ctx.GetStub().PutState(dataHashIndexKey, []byte(primaryCompositeKey))
		if err != nil {
			logger.Errorf("Failed to put dataHash index for rollback %s hash %s to world state: %v", name, targetEntry.DataHash, err)
			return nil, fmt.Errorf("failed to store index for rollback")
		}
	}

	// 9. Update the latest pointers to reflect the new rollback checkpoint
	currentCheckpointState.LatestVersion = newVersion
	currentCheckpointState.LatestHash = targetEntry.DataHash // The new latest hash is the target hash
	checkpointStateJSON, err = json.Marshal(currentCheckpointState)
	if err != nil {
		logger.Errorf("Failed to marshal updated checkpoint state for %s after rollback: %v", name, err)
		return nil, fmt.Errorf("failed to update latest state after rollback")
	}
	err = ctx.GetStub().PutState(name, checkpointStateJSON)
	if err != nil {
		logger.Errorf("Failed to update latest checkpoint state for %s after rollback: %v", name, err)
		return nil, fmt.Errorf("failed to update latest state after rollback")
	}

	logger.Infof("Checkpoint %s logically rolled back to hash %s (version %d) with TxID %s", name, targetEntry.DataHash, newVersion, ctx.GetStub().GetTxID())
	return &rollbackCheckpoint, nil
}

// main function to start the chaincode.
func main() {
	chaincode, err := contractapi.NewChaincode(&SmartContract{})
	if err != nil {
		fmt.Printf("Error creating checkpoint chaincode: %s", err.Error())
		return
	}

	if err := chaincode.Start(); err != nil {
		fmt.Printf("Error starting checkpoint chaincode: %s", err.Error())
	}
}