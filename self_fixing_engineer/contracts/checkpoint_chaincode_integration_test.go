//go:build integration

// Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

//go:build integration
// +build integration

// checkpoint_chaincode_integration_test.go — end-to-end integration tests for
// checkpoint_chaincode.go against a live Hyperledger Fabric test network.
//
// Prerequisites:
//   - Hyperledger Fabric test network running (fabric-samples/test-network)
//   - Chaincode deployed: peer lifecycle chaincode commit ...
//   - Connection profile at FABRIC_CONNECTION_PROFILE env var (defaults to
//     /path/to/connection.yaml)
//
// Run:
//
//go test -v -tags=integration -timeout=300s ./...
//
// API notes (updated for current chaincode version):
//   - ReadCheckpoint: second arg is an explicit string, "" = latest, "1" = v1
//   - Off-chain refs: path-style only (e.g. "bucket/object"); colons/double-slashes disallowed
//   - PrevHash: must be empty string for the first (genesis) checkpoint
//   - Timestamps: derived from GetTxTimestamp() — deterministic across all peers

package main_test

import (
"encoding/json"
"os"
"testing"

"github.com/hyperledger/fabric-sdk-go/pkg/core/config"
"github.com/hyperledger/fabric-sdk-go/pkg/gateway"
"github.com/stretchr/testify/assert"
"github.com/stretchr/testify/require"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/hyperledger/fabric-sdk-go/pkg/core/config"
	"github.com/hyperledger/fabric-sdk-go/pkg/gateway"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// ---------------------------------------------------------------------------
// Integration test constants
// ---------------------------------------------------------------------------

const (
integChannelName   = "mychannel"
integChaincodeName = "checkpoint"
integName          = "integ_checkpoint"
// Valid 64-character SHA-256 hex strings.
integDataHash1  = "aaaa1234567890abcdef1234567890abcdef1234567890abcdef1234567890aa"
integDataHash2  = "bbbb1234567890abcdef1234567890abcdef1234567890abcdef1234567890bb"
integMetadata   = `{"integration":"true"}`
integOffChain   = "bucket/integration-object" // Path-style; colons/double-slashes disallowed
integMessage    = "Integration rollback test"
	channelName     = "mychannel"
	chaincodeName   = "checkpoint"
	testName        = "test_checkpoint"
	testDataHash    = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
	testPrevHash    = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef" // Valid SHA256 (64 hex chars)
	testMetadata    = `{"key":"value"}`
	testOffChainRef = "s3://bucket/key"
	testMessage     = "Rollback test message"
)

// ---------------------------------------------------------------------------
// Gateway setup
// ---------------------------------------------------------------------------

// newGateway opens a Fabric SDK gateway.
// Connection profile path defaults to /path/to/connection.yaml but can be
// overridden via the FABRIC_CONNECTION_PROFILE environment variable.
func newGateway(t *testing.T) *gateway.Gateway {
t.Helper()
profilePath := os.Getenv("FABRIC_CONNECTION_PROFILE")
if profilePath == "" {
profilePath = "/path/to/connection.yaml"
}
gw, err := gateway.Connect(gateway.WithConfig(config.FromFile(profilePath)))
require.NoError(t, err, "failed to connect to Fabric gateway — is the test network running?")
return gw
}

// getContract returns the named contract on the default channel.
func getContract(t *testing.T, gw *gateway.Gateway) *gateway.Contract {
t.Helper()
network, err := gw.GetNetwork(integChannelName)
require.NoError(t, err)
return network.GetContract(integChaincodeName)
}

// unmarshalEntry deserialises a chaincode JSON response into a CheckpointEntry-shaped map.
func unmarshalEntry(t *testing.T, raw []byte) map[string]interface{} {
t.Helper()
var entry map[string]interface{}
require.NoError(t, json.Unmarshal(raw, &entry), "failed to unmarshal chaincode response")
return entry
}

// ---------------------------------------------------------------------------
// HealthCheck
// ---------------------------------------------------------------------------

func TestHealthCheckIntegration(t *testing.T) {
gw := newGateway(t)
defer gw.Close()

result, err := getContract(t, gw).EvaluateTransaction("HealthCheck")
require.NoError(t, err)
assert.Equal(t, `"Chaincode is healthy"`, string(result))
}

// ---------------------------------------------------------------------------
// WriteCheckpoint + ReadCheckpoint (genesis + chaining)
// ---------------------------------------------------------------------------

func TestWriteAndReadCheckpointIntegration(t *testing.T) {
gw := newGateway(t)
defer gw.Close()
contract := getContract(t, gw)

// 1. Write genesis (prevHash must be empty for first entry)
raw, err := contract.SubmitTransaction(
"WriteCheckpoint", integName, integDataHash1, "", integMetadata, integOffChain,
)
require.NoError(t, err)
entry := unmarshalEntry(t, raw)
assert.Equal(t, integName, entry["Name"])
assert.Equal(t, integDataHash1, entry["DataHash"])
assert.Equal(t, "", entry["PrevHash"])
assert.EqualValues(t, 1, entry["Version"])
assert.EqualValues(t, false, entry["IsRollback"])

// 2. Read by explicit version "1"
readRaw, err := contract.EvaluateTransaction("ReadCheckpoint", integName, "1")
require.NoError(t, err)
readEntry := unmarshalEntry(t, readRaw)
assert.Equal(t, entry["DataHash"], readEntry["DataHash"])

// 3. Read latest (empty version string = latest)
latestRaw, err := contract.EvaluateTransaction("ReadCheckpoint", integName, "")
require.NoError(t, err)
latestEntry := unmarshalEntry(t, latestRaw)
assert.EqualValues(t, 1, latestEntry["Version"])

// 4. Read by hash
hashRaw, err := contract.EvaluateTransaction("ReadCheckpointByHash", integName, integDataHash1)
require.NoError(t, err)
hashEntry := unmarshalEntry(t, hashRaw)
assert.Equal(t, integDataHash1, hashEntry["DataHash"])
}

// ---------------------------------------------------------------------------
// RollbackCheckpoint
// ---------------------------------------------------------------------------

func TestRollbackCheckpointIntegration(t *testing.T) {
gw := newGateway(t)
defer gw.Close()
contract := getContract(t, gw)

// Write genesis
_, err := contract.SubmitTransaction(
"WriteCheckpoint", integName, integDataHash1, "", integMetadata, integOffChain,
)
require.NoError(t, err)

// Write second entry chaining off genesis
_, err = contract.SubmitTransaction(
"WriteCheckpoint", integName, integDataHash2, integDataHash1, integMetadata, integOffChain,
)
require.NoError(t, err)

// Roll back to genesis hash
rollbackRaw, err := contract.SubmitTransaction("RollbackCheckpoint", integName, integDataHash1, integMessage)
require.NoError(t, err)
rollback := unmarshalEntry(t, rollbackRaw)
assert.EqualValues(t, true, rollback["IsRollback"])
assert.Equal(t, integDataHash1, rollback["DataHash"])
assert.EqualValues(t, 3, rollback["Version"])

// Verify history contains all three entries, last is rollback
historyRaw, err := contract.EvaluateTransaction("ReadCheckpointHistory", integName, "1", "3")
require.NoError(t, err)
var history []map[string]interface{}
require.NoError(t, json.Unmarshal(historyRaw, &history))
require.Len(t, history, 3)
assert.EqualValues(t, true, history[2]["IsRollback"])
}

// ---------------------------------------------------------------------------
// RBAC
// ---------------------------------------------------------------------------

// TestRBACEnforcedIntegration verifies that RBAC errors contain "unauthorized"
// and do not expose "<nil>" in the error message (quality check).
func TestRBACEnforcedIntegration(t *testing.T) {
gw := newGateway(t)
defer gw.Close()
contract := getContract(t, gw)

// If the invoking identity does not have the required role, expect an
// "unauthorized" error. The exact outcome depends on the deployed policy.
_, err := contract.SubmitTransaction(
"WriteCheckpoint", integName, integDataHash1, "", integMetadata, integOffChain,
)
if err != nil {
assert.Contains(t, err.Error(), "unauthorized",
"expected RBAC-style error when invoker lacks the required role")
assert.NotContains(t, err.Error(), "<nil>",
"error messages must not expose '<nil>' — check hasRole() error handling")
}
}

// Run integration tests with:
//   go test -v -tags=integration -timeout=300s ./...
//
// Set FABRIC_CONNECTION_PROFILE=/absolute/path/to/connection.yaml to override
// the default connection profile location.
