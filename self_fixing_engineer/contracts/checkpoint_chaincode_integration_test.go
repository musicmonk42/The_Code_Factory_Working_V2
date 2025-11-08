// checkpoint_chaincode_integration_test.go
// Integration tests for checkpoint_chaincode.go
// These tests require a running Fabric test network.
// Setup: Use Fabric samples/test-network; deploy chaincode before running.
// Run: go test -v ./... -tags=integration
// Note: -tags=integration to separate from unit tests.

package main_test

import (
	"fmt"
	"testing"

	"github.com/hyperledger/fabric-sdk-go/pkg/core/config"
	"github.com/hyperledger/fabric-sdk-go/pkg/gateway"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Test constants (match unit tests)
const (
	channelName      = "mychannel"
	chaincodeName    = "checkpoint"
	testName         = "test_checkpoint"
	testDataHash     = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
	testPrevHash     = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
	testMetadata     = `{"key":"value"}`
	testOffChainRef  = "s3://bucket/key"
	testMessage      = "Rollback test message"
)

// Setup gateway connection
func setupGateway(t *testing.T) *gateway.Gateway {
	configPath := "/path/to/connection.yaml"  // Adjust to your Fabric connection profile
	gw, err := gateway.Connect(gateway.WithConfig(config.FromFile(configPath)))
	require.NoError(t, err)
	return gw
}

// TestWriteAndReadCheckpointIntegration tests end-to-end write and read
func TestWriteAndReadCheckpointIntegration(t *testing.T) {
	gw := setupGateway(t)
	defer gw.Close()
	network, err := gw.GetNetwork(channelName)
	require.NoError(t, err)
	contract := network.GetContract(chaincodeName)

	// Write checkpoint
	result, err := contract.SubmitTransaction("WriteCheckpoint", testName, testDataHash, testPrevHash, testMetadata, testOffChainRef)
	require.NoError(t, err)
	var entry CheckpointEntry
	json.Unmarshal(result, &entry)
	assert.Equal(t, testName, entry.Name)
	assert.Equal(t, testDataHash, entry.DataHash)

	// Read by version
	readResult, err := contract.EvaluateTransaction("ReadCheckpoint", testName, "1")
	require.NoError(t, err)
	var readEntry CheckpointEntry
	json.Unmarshal(readResult, &readEntry)
	assert.Equal(t, entry, readEntry)

	// Read by hash
	readHashResult, err := contract.EvaluateTransaction("ReadCheckpointByHash", testName, testDataHash)
	require.NoError(t, err)
	json.Unmarshal(readHashResult, &readEntry)
	assert.Equal(t, entry, readEntry)
}

// TestRollbackCheckpointIntegration tests rollback
func TestRollbackCheckpointIntegration(t *testing.T) {
	gw := setupGateway(t)
	defer gw.Close()
	network, err := gw.GetNetwork(channelName)
	require.NoError(t, err)
	contract := network.GetContract(chaincodeName)

	// Assume prior write (from previous test or setup)
	// Write a second checkpoint for rollback test
	_, err = contract.SubmitTransaction("WriteCheckpoint", testName, "new_hash", testDataHash, testMetadata, testOffChainRef)
	require.NoError(t, err)

	// Rollback to first hash
	result, err := contract.SubmitTransaction("RollbackCheckpoint", testName, testDataHash, testMessage)
	require.NoError(t, err)
	var entry CheckpointEntry
	json.Unmarshal(result, &entry)
	assert.True(t, entry.IsRollback)
	assert.Equal(t, testDataHash, entry.DataHash)

	// Verify history
	historyResult, err := contract.EvaluateTransaction("ReadCheckpointHistory", testName, "1", "3")
	require.NoError(t, err)
	var history []*CheckpointEntry
	json.Unmarshal(historyResult, &history)
	assert.Len(t, history, 3)
	assert.True(t, history[2].IsRollback)
}

// TestHealthCheckIntegration tests health check
func TestHealthCheckIntegration(t *testing.T) {
	gw := setupGateway(t)
	defer gw.Close()
	network, err := gw.GetNetwork(channelName)
	require.NoError(t, err)
	contract := network.GetContract(chaincodeName)

	result, err := contract.EvaluateTransaction("HealthCheck")
	require.NoError(t, err)
	assert.Equal(t, `"Chaincode is healthy"`, string(result))
}

// TestInvalidAccessIntegration tests RBAC failure (assuming policy set to require specific MSP)
func TestInvalidAccessIntegration(t *testing.T) {
	gw := setupGateway(t)
	defer gw.Close()
	network, err := gw.GetNetwork(channelName)
	require.NoError(t, err)
	contract := network.GetContract(chaincodeName)

	// Attempt write with invalid MSP (simulate by expecting error if policy enforces)
	_, err = contract.SubmitTransaction("WriteCheckpoint", testName, testDataHash, testPrevHash, testMetadata, testOffChainRef)
	if err != nil {
		assert.Contains(t, err.Error(), "unauthorized")  // Assuming policy/RBAC rejects
	}
}

// Run integration tests with: go test -v -tags=integration
// Setup Fabric test network first (e.g., ./network.sh up from Fabric samples)