/**
 * Standalone Chat Widget Embed Script
 * This script loads the exact same chat widget from base.html with zero custom styling
 * Just brings the button from base.html to any website
 */

(function() {
    // Configuration (will be replaced with actual values when served)
    const config = {
        serverUrl: '{{server_url}}',
        primaryColor: '{{primary_color}}' || '#4674C6',
        widgetIconColor: '{{widget_icon_color}}' || '#4674C6',
        position: '{{position}}' || 'right',
        welcomeMessage: '{{welcome_message}}',
        companyName: '{{company_name}}' || 'Customer Support',
        logoUrl: '{{logo_url}}'
    };

    // Debug configuration
    console.log('Chat Widget Config:', config);

    // Generate or retrieve browser UUID for visitor identification
    let browserUUID = localStorage.getItem('browser_uuid');
    if (!browserUUID) {
        if (window.crypto && window.crypto.randomUUID) {
            browserUUID = window.crypto.randomUUID();
        } else {
            browserUUID = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                const r = Math.random() * 16 | 0;
                const v = c === 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
        }
        localStorage.setItem('browser_uuid', browserUUID);
    }

    // Make browserUUID available globally
    window.browserUUID = browserUUID;

    // Load the exact same resources as base.html
    function loadBaseResources() {
        // Load Tailwind CSS
        if (!document.querySelector('script[src*="tailwindcss"]')) {
            const tailwindScript = document.createElement('script');
            tailwindScript.src = 'https://cdn.tailwindcss.com';
            document.head.appendChild(tailwindScript);
        }

        // Load Google Material Icons
        if (!document.querySelector('link[href*="material-icons"]')) {
            const materialIcons = document.createElement('link');
            materialIcons.href = 'https://fonts.googleapis.com/icon?family=Material+Icons';
            materialIcons.rel = 'stylesheet';
            document.head.appendChild(materialIcons);
        }

        // Load Google Fonts (Inter)
        if (!document.querySelector('link[href*="Inter"]')) {
            const googleFonts = document.createElement('link');
            googleFonts.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap';
            googleFonts.rel = 'stylesheet';
            document.head.appendChild(googleFonts);
        }

        // Load Font Awesome
        if (!document.querySelector('link[href*="font-awesome"]')) {
            const fontAwesome = document.createElement('link');
            fontAwesome.rel = 'stylesheet';
            fontAwesome.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.2.0/css/all.min.css';
            document.head.appendChild(fontAwesome);
        }

        // Add Tailwind config (same as base.html)
        if (!document.querySelector('#tailwind-config')) {
            const tailwindConfig = document.createElement('script');
            tailwindConfig.id = 'tailwind-config';
            tailwindConfig.textContent = `
                tailwind.config = {
                    theme: {
                        extend: {
                            colors: {
                                primary: {
                                    50: '#f0f4ff',
                                    100: '#e8eef8',
                                    200: '#d1ddf0',
                                    300: '#a8c1e3',
                                    400: '#7ba0d3',
                                    500: '#4674C6',
                                    600: '#3a5fa8',
                                    700: '#2f4d8a',
                                    800: '#253e6f',
                                    900: '#1d3159',
                                    950: '#141f3a',
                                },
                                secondary: {
                                    50: '#f5f8fa',
                                    100: '#e1f5fe',
                                    200: '#b3e5fc',
                                    300: '#81d4fa',
                                    400: '#4fc3f7',
                                    500: '#29b6f6',
                                    600: '#00bcd4',
                                    700: '#0097a7',
                                    800: '#00838f',
                                    900: '#006064',
                                    950: '#004d40',
                                },
                            },
                            fontFamily: {
                                sans: ['Inter', 'sans-serif'],
                            },
                            boxShadow: {
                                'soft': '0 2px 15px 0 rgba(0, 0, 0, 0.05)',
                                'medium': '0 4px 20px 0 rgba(0, 0, 0, 0.1)',
                            },
                        },
                    },
                }
            `;
            document.head.appendChild(tailwindConfig);
        }
    }

    // Create the exact same chat widget HTML from base.html
    function createChatWidget() {
        // Chat Widget Button (exact copy from base.html)
        const chatButton = document.createElement('div');
        chatButton.id = 'chat-widget-button';
        chatButton.className = 'fixed bottom-6 right-6 w-16 h-16 rounded-full shadow-lg flex items-center justify-center cursor-pointer z-50 transition-all';
        chatButton.style.backgroundColor = config.widgetIconColor;

        const buttonIcon = document.createElement('span');
        buttonIcon.className = 'material-icons text-white';
        buttonIcon.textContent = 'support_agent';
        chatButton.appendChild(buttonIcon);

        // Chat Widget Popup (exact copy from base.html)
        const chatContainer = document.createElement('div');
        chatContainer.id = 'chat-widget-container';
        chatContainer.className = 'fixed bottom-0 right-0 z-50 transition-all duration-300 transform translate-y-full';
        chatContainer.style.width = 'min(360px, calc(100% - 20px))';
        chatContainer.style.height = 'min(550px, 85vh)';
        chatContainer.style.maxHeight = '85vh';
        chatContainer.style.marginRight = '10px';
        chatContainer.style.marginBottom = '0px';

        const chatWindow = document.createElement('div');
        chatWindow.className = 'bg-white rounded-t-lg shadow-lg overflow-hidden flex flex-col h-full';

        // Header (exact copy from base.html)
        const header = document.createElement('div');
        header.className = 'bot-heading bg-gradient-to-r from-primary-600 to-primary-700 text-white p-4 flex justify-between items-center';
        header.style.background = `linear-gradient(to right, #0F4173, #0a3055)`;
        header.style.color = 'white';

        const title = document.createElement('h3');
        title.className = 'text-lg font-semibold truncate max-w-[50%]';
        title.style.color = 'white';
        title.style.fontSize = '18px';
        title.style.fontWeight = '600';
        title.style.margin = '0';
        title.style.maxWidth = '50%';
        title.style.overflow = 'hidden';
        title.style.textOverflow = 'ellipsis';
        title.style.whiteSpace = 'nowrap';
        title.textContent = config.companyName;

        const controls = document.createElement('div');
        controls.className = 'flex items-center space-x-2 sm:space-x-4';
        controls.style.display = 'flex';
        controls.style.alignItems = 'center';
        controls.style.gap = '8px';

        // Chat mode selector (exact copy from base.html)
        const modeSelector = document.createElement('div');
        modeSelector.className = 'chat-mode-selector';

        const modeContainer = document.createElement('div');
        modeContainer.className = 'flex items-center';

        const modeLabel = document.createElement('span');
        modeLabel.className = 'text-xs text-white/80 mr-2';
        modeLabel.id = 'chat-mode-label';
        modeLabel.style.fontSize = '12px';
        modeLabel.style.color = 'rgba(255, 255, 255, 0.8)';
        modeLabel.style.marginRight = '8px';
        modeLabel.textContent = 'Human';

        const toggleLabel = document.createElement('label');
        toggleLabel.className = 'relative inline-flex items-center cursor-pointer';

        const toggleInput = document.createElement('input');
        toggleInput.type = 'checkbox';
        toggleInput.id = 'widget-chat-mode-toggle';
        toggleInput.className = 'sr-only peer';

        const toggleDiv = document.createElement('div');
        toggleDiv.className = 'w-9 h-5 bg-gray-200 rounded-full peer peer-checked:after:translate-x-full after:content-[\'\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-secondary-300';
        toggleDiv.style.transition = 'all 0.3s ease';

        toggleLabel.appendChild(toggleInput);
        toggleLabel.appendChild(toggleDiv);
        modeContainer.appendChild(modeLabel);
        modeContainer.appendChild(toggleLabel);
        modeSelector.appendChild(modeContainer);

        // Minimize button (exact copy from base.html)
        const minimizeBtn = document.createElement('button');
        minimizeBtn.id = 'chat-widget-minimize';
        minimizeBtn.className = 'text-white hover:text-gray-200 w-8 h-8 flex items-center justify-center bg-white/20 rounded-md';
        minimizeBtn.style.color = 'white';
        minimizeBtn.style.width = '32px';
        minimizeBtn.style.height = '32px';
        minimizeBtn.style.display = 'flex';
        minimizeBtn.style.alignItems = 'center';
        minimizeBtn.style.justifyContent = 'center';
        minimizeBtn.style.backgroundColor = 'rgba(255, 255, 255, 0.2)';
        minimizeBtn.style.borderRadius = '6px';
        minimizeBtn.style.border = 'none';
        minimizeBtn.style.cursor = 'pointer';

        const minimizeIcon = document.createElement('span');
        minimizeIcon.className = 'material-icons';
        minimizeIcon.style.fontSize = '20px';
        minimizeIcon.style.color = 'white';
        minimizeIcon.textContent = 'minimize';
        minimizeBtn.appendChild(minimizeIcon);

        controls.appendChild(modeSelector);
        controls.appendChild(minimizeBtn);
        header.appendChild(title);
        header.appendChild(controls);

        // User Details Section (exact copy from base.html)
        const userDetailsSection = document.createElement('div');
        userDetailsSection.id = 'user-details-section';
        userDetailsSection.className = 'bg-white border-b border-gray-200 p-3';
        userDetailsSection.style.display = 'none';

        // Chat iframe (exact copy from base.html)
        const iframe = document.createElement('iframe');
        iframe.id = 'chat-iframe';
        iframe.src = 'about:blank';
        iframe.className = 'w-full flex-grow border-0';

        // Assemble the widget
        chatWindow.appendChild(header);
        chatWindow.appendChild(userDetailsSection);
        chatWindow.appendChild(iframe);
        chatContainer.appendChild(chatWindow);

        return {
            chatButton,
            chatContainer,
            iframe,
            toggleInput,
            modeLabel,
            minimizeBtn,
            userDetailsSection
        };
    }

    // Add the exact same CSS styles from base.html
    function addBaseStyles() {
        if (document.querySelector('#embed-base-styles')) return;

        const style = document.createElement('style');
        style.id = 'embed-base-styles';
        style.textContent = `
            /* Chat widget control styles - exact copy from base.html */
            #chat-widget-button {
                background-color: ${config.widgetIconColor} !important;
            }

            #chat-widget-button:hover {
                filter: brightness(0.9) !important;
                transform: scale(1.05) !important;
            }

            #chat-widget-button .material-icons {
                font-size: 32px;
            }

            #chat-widget-minimize {
                background-color: rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                transition: all 0.2s ease;
            }

            #chat-widget-minimize:hover {
                background-color: rgba(255, 255, 255, 0.35);
                transform: scale(1.05);
            }

            #chat-widget-minimize .material-icons {
                font-size: 20px;
                line-height: 1;
            }

            /* Fix chat container positioning */
            #chat-widget-container {
                bottom: 0 !important;
                right: 0 !important;
                margin-right: 10px !important;
                margin-bottom: 0 !important;
            }

            /* Ensure proper z-index */
            #chat-widget-button {
                z-index: 9999 !important;
            }

            #chat-widget-container {
                z-index: 9998 !important;
            }

            /* Ensure header has proper blue gradient styling */
            .bot-heading {
                background: linear-gradient(to right, #0F4173, #0a3055) !important;
                color: white !important;
                padding: 16px !important;
                display: flex !important;
                justify-content: space-between !important;
                align-items: center !important;
            }

            .bot-heading h3 {
                color: white !important;
                font-size: 18px !important;
                font-weight: 600 !important;
                margin: 0 !important;
                max-width: 50% !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                white-space: nowrap !important;
            }

            /* Ensure controls section styling */
            .bot-heading .flex {
                display: flex !important;
                align-items: center !important;
                gap: 8px !important;
            }

            /* Mode label styling */
            #chat-mode-label {
                color: rgba(255, 255, 255, 0.8) !important;
                font-size: 12px !important;
                margin-right: 8px !important;
            }

            /* User details section styles - exact copy from base.html */
            #user-details-section {
                display: none; /* Hidden by default, shown when needed */
            }

            .user-details-banner {
                display: flex;
                align-items: center;
                justify-content: space-between;
            }

            .user-details-banner .banner-text {
                display: flex;
                align-items: center;
                color: #0F4173;
                font-weight: 500;
                font-size: 14px;
            }

            .user-details-banner .banner-text i {
                margin-right: 8px;
                font-size: 16px;
                color: #0F4173;
            }

            .user-details-banner .add-details-btn {
                background-color: #0F4173;
                color: white;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: 500;
                transition: all 0.2s ease;
                border: none;
                cursor: pointer;
            }

            .user-details-banner .add-details-btn:hover {
                background-color: #0a3055;
                transform: translateY(-1px);
            }

            .user-details-display {
                display: flex;
                align-items: center;
                justify-content: space-between;
            }

            .user-details-display .details-text {
                font-size: 14px;
                color: #333;
            }

            .user-details-display .details-text .user-name {
                font-weight: 600;
                color: #0F4173;
            }

            .user-details-display .details-text .user-email {
                font-size: 12px;
                color: #666;
                margin-left: 4px;
            }

            .user-details-display .edit-details-btn {
                color: #0F4173;
                background: none;
                border: none;
                font-size: 12px;
                cursor: pointer;
                display: flex;
                align-items: center;
                padding: 4px 8px;
                border-radius: 4px;
                transition: all 0.2s ease;
            }

            .user-details-display .edit-details-btn:hover {
                background-color: rgba(15, 65, 115, 0.1);
            }

            .user-details-display .edit-details-btn i {
                font-size: 14px;
                margin-right: 4px;
            }

            /* Enhanced toggle switch styling - exact copy from base.html */
            #widget-chat-mode-toggle:checked + div {
                background-color: #5680a7 !important;
            }

            .peer-checked\\:bg-secondary-300:checked ~ div {
                background-color: #0F4173 !important;
            }

            /* Direct toggle styling override */
            .chat-mode-selector label div {
                transition: all 0.3s ease;
                background-color: #edf2f7 !important; /* Much lighter gray with blue tint */
            }

            .chat-mode-selector input:checked + div {
                background-color: #4674C6 !important;
            }

            /* Ensure control buttons are visible on small screens */
            @media (max-width: 640px) {
                .chat-mode-selector {
                    margin-right: 4px;
                }

                #chat-widget-button {
                    width: 60px;
                    height: 60px;
                }

                #chat-widget-button .material-icons {
                    font-size: 28px;
                }

                #chat-widget-minimize {
                    width: 32px !important;
                    height: 32px !important;
                    margin-left: 8px;
                }
            }
        `;
        document.head.appendChild(style);
    }

    // Initialize the widget with exact same functionality as base.html
    function initWidget() {
        // Load all required resources first
        loadBaseResources();
        addBaseStyles();

        // Wait for Tailwind to load before creating elements
        setTimeout(() => {
            // Create the widget using exact same structure as base.html
            const {
                chatButton,
                chatContainer,
                iframe,
                toggleInput,
                modeLabel,
                minimizeBtn,
                userDetailsSection
            } = createChatWidget();

            // Add to DOM
            document.body.appendChild(chatButton);
            document.body.appendChild(chatContainer);

            // Copy the exact same JavaScript functionality from base.html
            let isOpen = false;
            let isMinimized = false;
            let currentChatMode = 'human';
            let roomId = null;
            let visitorDetails = { name: '', email: '' };

            // Add CSS transition for smooth animation
            chatContainer.style.transition = 'transform 0.3s ease-in-out';

            // EXACT SAME FUNCTIONS FROM BASE.HTML - Copy paste from base.html JavaScript

            // Open chat function (exact copy from base.html)
            function openChat() {
                if (!isOpen || isMinimized) {
                    // Load chat iframe if not already loaded
                    if (iframe.src === 'about:blank') {
                        const visitorId = browserUUID;

                        if (visitorId) {
                            console.log('Attempting to create chat room for visitor:', visitorId);
                            console.log('API URL:', `${config.serverUrl}/api/check_visitor`);

                            // First check if we need to create a new chat room
                            fetch(`${config.serverUrl}/api/check_visitor`, {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                    'X-Browser-UUID': browserUUID
                                },
                                body: JSON.stringify({ visitor_id: visitorId })
                            })
                            .then(response => {
                                if (!response.ok) {
                                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                                }
                                return response.json();
                            })
                            .then(data => {
                                if (data.success && data.room_id) {
                                    const iframeUrl = `${config.serverUrl}/chat_iframe/${data.room_id}`;
                                    console.log('Setting iframe URL to:', iframeUrl);
                                    iframe.src = iframeUrl;
                                } else {
                                    console.error('Error creating chat room:', data);
                                    // Fallback to direct visitor ID
                                    const fallbackUrl = `${config.serverUrl}/chat_iframe/${visitorId}`;
                                    console.log('Using fallback iframe URL:', fallbackUrl);
                                    iframe.src = fallbackUrl;
                                }
                            })
                            .catch(error => {
                                console.error('Error checking visitor:', error);
                                console.log('Falling back to direct visitor ID approach');
                                // Fallback - try to use the visitor ID directly
                                iframe.src = `${config.serverUrl}/chat_iframe/${visitorId}`;
                            });
                        } else {
                            console.error('No visitor ID available');
                        }
                    }

                    // Show chat container
                    chatContainer.style.transform = 'translateY(0)';
                    isOpen = true;
                    isMinimized = false;

                    // Hide the chat button
                    chatButton.style.display = 'none';
                }
            }

            // Minimize chat function (exact copy from base.html)
            function minimizeChat() {
                if (isOpen && !isMinimized) {
                    chatContainer.style.transform = 'translateY(100%)';
                    isMinimized = true;

                    // Show the chat button again
                    chatButton.style.display = 'flex';
                }
            }

            // Close chat function (exact copy from base.html)
            function closeChat() {
                if (isOpen) {
                    chatContainer.style.transform = 'translateY(100%)';
                    isOpen = false;
                    isMinimized = false;

                    // Show the chat button again
                    chatButton.style.display = 'flex';
                }
            }

            // Add event listeners (exact copy from base.html)
            chatButton.addEventListener('click', openChat);
            minimizeBtn.addEventListener('click', minimizeChat);

            // Handle chat mode toggle (exact copy from base.html)
            toggleInput.addEventListener('change', function() {
                if (!roomId) return;

                const isAiMode = this.checked;
                const newMode = isAiMode ? 'ai' : 'human';

                // Update the label immediately for better UX
                modeLabel.textContent = isAiMode ? 'AI' : 'Human';

                // If switching to human mode, check admin availability first
                if (newMode === 'human') {
                    fetch(`${config.serverUrl}/admin/api/check_human_mode_availability`)
                        .then(response => response.json())
                        .then(data => {
                            if (data.success && data.human_mode_available) {
                                updateChatMode(newMode);
                            } else {
                                this.checked = true;
                                modeLabel.textContent = 'AI';
                                showToastNotification(data.reason || 'Human support is not available right now.');
                            }
                        })
                        .catch(error => {
                            console.error('Error checking human mode availability:', error);
                            this.checked = true;
                            modeLabel.textContent = 'AI';
                        });
                } else {
                    updateChatMode(newMode);
                }
            });

            // Function to update chat mode (exact copy from base.html)
            function updateChatMode(newMode) {
                modeLabel.textContent = newMode === 'ai' ? 'AI' : 'Human';

                if (iframe.contentWindow) {
                    iframe.contentWindow.postMessage({
                        type: 'toggleChatMode',
                        mode: newMode
                    }, '*');
                }
            }

            // Listen for messages from the iframe (exact copy from base.html)
            window.addEventListener('message', function(event) {
                if (event.source === iframe.contentWindow) {
                    const data = event.data;

                    // Handle room ID message
                    if (data.type === 'roomId') {
                        roomId = data.roomId;

                        // Update the chat mode toggle based on current mode
                        if (data.chatMode === 'ai') {
                            toggleInput.checked = true;
                            modeLabel.textContent = 'AI';
                            currentChatMode = 'ai';
                        } else {
                            toggleInput.checked = false;
                            modeLabel.textContent = 'Human';
                            currentChatMode = 'human';
                        }
                    }

                    // Handle chat mode change message
                    if (data.type === 'chatModeChanged') {
                        if (data.mode === 'ai') {
                            toggleInput.checked = true;
                            modeLabel.textContent = 'AI';
                        } else {
                            toggleInput.checked = false;
                            modeLabel.textContent = 'Human';
                        }
                        currentChatMode = data.mode;
                    }

                    // Handle admin unavailable message
                    if (data.type === 'adminUnavailable') {
                        toggleInput.checked = true;
                        modeLabel.textContent = 'AI';
                        currentChatMode = 'ai';
                        showToastNotification('No admin is available right now. Please check back again later.');
                    }

                    // Handle visitor details update
                    if (data.type === 'visitorDetailsUpdated') {
                        visitorDetails.name = data.name || '';
                        visitorDetails.email = data.email || '';
                        updateUserDetailsSection(visitorDetails.name, visitorDetails.email);
                    }
                }
            });

            // Function to update user details section (exact copy from base.html)
            function updateUserDetailsSection(name, email) {
                const hasName = name && name.trim() !== '';
                const hasEmail = email && email.trim() !== '';

                if (hasName || hasEmail) {
                    userDetailsSection.innerHTML = `
                        <div class="user-details-display">
                            <div class="details-text">
                                ${hasName ? `Chatting as: <span class="user-name">${name}</span>` : ''}
                                ${hasEmail ? `<span class="user-email">(${email})</span>` : ''}
                            </div>
                            <button class="edit-details-btn" id="edit-user-details">
                                <i class="fas fa-pencil"></i> Edit
                            </button>
                        </div>
                    `;
                    userDetailsSection.style.display = 'block';

                    document.getElementById('edit-user-details').addEventListener('click', function() {
                        if (iframe.contentWindow) {
                            iframe.contentWindow.postMessage({ type: 'openDetailsModal' }, '*');
                        }
                    });
                } else {
                    userDetailsSection.innerHTML = `
                        <div class="user-details-banner">
                            <div class="banner-text">
                                <i class="fas fa-user-pen"></i>
                                <span>Add your details to help us serve you better</span>
                            </div>
                            <button class="add-details-btn" id="add-user-details">
                                Add Details
                            </button>
                        </div>
                    `;
                    userDetailsSection.style.display = 'block';

                    document.getElementById('add-user-details').addEventListener('click', function() {
                        if (iframe.contentWindow) {
                            iframe.contentWindow.postMessage({ type: 'openDetailsModal' }, '*');
                        }
                    });
                }
            }

            // Function to show a toast notification (exact copy from base.html)
            function showToastNotification(message) {
                let toast = document.getElementById('embed-toast-notification');
                if (!toast) {
                    toast = document.createElement('div');
                    toast.id = 'embed-toast-notification';
                    toast.style.position = 'fixed';
                    toast.style.bottom = '80px';
                    toast.style.right = '20px';
                    toast.style.backgroundColor = '#333';
                    toast.style.color = 'white';
                    toast.style.padding = '12px 20px';
                    toast.style.borderRadius = '4px';
                    toast.style.boxShadow = '0 2px 10px rgba(0,0,0,0.2)';
                    toast.style.zIndex = '10000';
                    toast.style.transition = 'opacity 0.3s ease-in-out';
                    toast.style.opacity = '0';
                    document.body.appendChild(toast);
                }

                toast.textContent = message;
                toast.style.opacity = '1';

                setTimeout(() => {
                    toast.style.opacity = '0';
                    setTimeout(() => {
                        if (toast.parentNode) {
                            toast.parentNode.removeChild(toast);
                        }
                    }, 300);
                }, 3000);
            }
        }, 1000); // Wait 1 second for Tailwind to load
    }

    // Initialize when DOM is loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initWidget);
    } else {
        initWidget();
    }
})();
