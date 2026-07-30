package mcp

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

type ToolDefinition struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	InputSchema json.RawMessage `json:"inputSchema"`
}

type Client struct {
	BaseURL    string
	SessionID  string
	HttpClient *http.Client
	sseResp    *http.Response
	closeChan  chan struct{}
}

// Response structs for parsing MCP messages
type toolListResponse struct {
	Result struct {
		Tools []ToolDefinition `json:"tools"`
	} `json:"result"`
}

type toolCallResponse struct {
	Result struct {
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
		IsError bool `json:"isError"`
	} `json:"result"`
}

// sseMessage helper to parse the synchronous SSE wrapper from POST responses
type sseMessage struct {
	Event string
	Data  string
}

func parseSSEData(body []byte) (string, error) {
	lines := strings.Split(string(body), "\n")
	var dataLine string
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "data:") {
			dataLine = strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			break
		}
	}
	if dataLine == "" {
		return "", fmt.Errorf("no data field found in SSE response body: %s", string(body))
	}
	return dataLine, nil
}

func NewClient(baseURL string) (*Client, error) {
	client := &Client{
		BaseURL:    baseURL,
		HttpClient: &http.Client{},
		closeChan:  make(chan struct{}),
	}

	// 1. Handshake: Get session ID
	resp, err := http.Get(baseURL)
	if err != nil {
		return nil, fmt.Errorf("handshake failed: %w", err)
	}
	defer resp.Body.Close()

	client.SessionID = resp.Header.Get("Mcp-Session-Id")
	if client.SessionID == "" {
		return nil, fmt.Errorf("handshake failed: missing Mcp-Session-Id header")
	}

	// 2. Connect to SSE Stream
	req, err := http.NewRequest("GET", baseURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("Mcp-Session-Id", client.SessionID)

	sseResp, err := client.HttpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("SSE connection failed: %w", err)
	}
	client.sseResp = sseResp

	if sseResp.StatusCode != 200 {
		body, _ := io.ReadAll(sseResp.Body)
		sseResp.Body.Close()
		return nil, fmt.Errorf("failed to connect to SSE: status %s, body: %s", sseResp.Status, body)
	}

	// Start a goroutine to read the SSE stream to prevent buffer backup
	go func() {
		reader := bufio.NewReader(sseResp.Body)
		for {
			select {
			case <-client.closeChan:
				return
			default:
				_, err := reader.ReadString('\n')
				if err != nil {
					return
				}
			}
		}
	}()

	return client, nil
}

func (c *Client) Close() error {
	close(c.closeChan)
	if c.sseResp != nil {
		return c.sseResp.Body.Close()
	}
	return nil
}

func (c *Client) sendPOST(payload string) ([]byte, error) {
	req, err := http.NewRequest("POST", c.BaseURL, bytes.NewBufferString(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("Mcp-Session-Id", c.SessionID)

	resp, err := c.HttpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("POST request failed: status %s, body: %s", resp.Status, body)
	}

	// Parse SSE wrapper from the body
	data, err := parseSSEData(body)
	if err != nil {
		return nil, fmt.Errorf("failed to parse SSE wrapper: %w", err)
	}

	return []byte(data), nil
}

func (c *Client) Initialize() error {
	initJSON := `{
		"jsonrpc": "2.0",
		"method": "initialize",
		"id": 1,
		"params": {
			"protocolVersion": "2024-11-05",
			"capabilities": {},
			"clientInfo": {
				"name": "go-mcp-agent",
				"version": "1.0.0"
			}
		}
	}`

	_, err := c.sendPOST(initJSON)
	if err != nil {
		return fmt.Errorf("initialize failed: %w", err)
	}

	// Send initialized notification as required by MCP
	initializedNotification := `{
		"jsonrpc": "2.0",
		"method": "notifications/initialized"
	}`
	// Note: Notifications don't get a response, but our sendPOST expects one. 
	// The server might return HTTP 200 with no message or an empty SSE.
	// Let's send it using a simple POST without expecting SSE parsing if it errors.
	req, err := http.NewRequest("POST", c.BaseURL, bytes.NewBufferString(initializedNotification))
	if err == nil {
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("Mcp-Session-Id", c.SessionID)
		if resp, err := c.HttpClient.Do(req); err == nil {
			resp.Body.Close()
		}
	}

	return nil
}

func (c *Client) ListTools() ([]ToolDefinition, error) {
	reqJSON := `{
		"jsonrpc": "2.0",
		"method": "tools/list",
		"id": 2,
		"params": {}
	}`

	respBytes, err := c.sendPOST(reqJSON)
	if err != nil {
		return nil, err
	}

	var res toolListResponse
	if err := json.Unmarshal(respBytes, &res); err != nil {
		return nil, fmt.Errorf("failed to unmarshal tools/list response: %w (body: %s)", err, string(respBytes))
	}

	return c.ResultTools(res)
}

func (c *Client) ResultTools(res toolListResponse) ([]ToolDefinition, error) {
	return res.Result.Tools, nil
}

func (c *Client) CallTool(name string, arguments map[string]interface{}) (string, error) {
	argsBytes, err := json.Marshal(arguments)
	if err != nil {
		return "", err
	}

	reqJSON := fmt.Sprintf(`{
		"jsonrpc": "2.0",
		"method": "tools/call",
		"id": 3,
		"params": {
			"name": "%s",
			"arguments": %s
		}
	}`, name, string(argsBytes))

	respBytes, err := c.sendPOST(reqJSON)
	if err != nil {
		return "", err
	}

	var res toolCallResponse
	if err := json.Unmarshal(respBytes, &res); err != nil {
		return "", fmt.Errorf("failed to unmarshal tools/call response: %w (body: %s)", err, string(respBytes))
	}

	if res.Result.IsError {
		return "", fmt.Errorf("tool returned error response: %v", res.Result.Content)
	}

	if len(res.Result.Content) == 0 {
		return "", fmt.Errorf("tool returned empty response")
	}

	// Concatenate all text outputs
	var output string
	for _, content := range res.Result.Content {
		if content.Type == "text" {
			output += content.Text
		}
	}

	return output, nil
}
