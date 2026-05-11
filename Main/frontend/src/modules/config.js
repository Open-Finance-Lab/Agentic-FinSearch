// config.js

import { buildBackendUrl } from './backendConfig.js';

// Available models - will be populated from backend
let availableModels = [];
let modelDetails = {}; // Store full model details

// Initialize a single selected model
let selectedModel = "FinGPT";

// Fetch available models from backend
async function fetchAvailableModels() {
    const response = await fetch(buildBackendUrl('/api/get_available_models/'), { credentials: 'include' });
    if (!response.ok) {
        throw new Error(`Failed to fetch models from backend. HTTP error! status: ${response.status}`);
    }
    const data = await response.json();

    modelDetails = {};
    data.models.forEach(model => {
        modelDetails[model.id] = model;
    });
    
    availableModels = data.models.map(model => model.id);
    
    if (availableModels.length === 0) {
        throw new Error('No models available from backend');
    }
    
    // If current selected model is not in the list, select the first available
    if (!availableModels.includes(selectedModel) && availableModels.length > 0) {
        selectedModel = availableModels[0];
    }
    
    return availableModels;
}

function getSelectedModel() {
    return selectedModel;
}

function setSelectedModel(model) {
    selectedModel = model;
}

function getAvailableModels() {
    return availableModels;
}

function getModelDetails() {
    return modelDetails;
}

export { availableModels, selectedModel, getSelectedModel, setSelectedModel, fetchAvailableModels, getAvailableModels, getModelDetails };
