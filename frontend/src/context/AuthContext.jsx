import React, { createContext, useContext, useState, useEffect } from 'react';
import { fetchCurrentUser, loginUser, logoutUser, fetchSetupStatus, signupAdmin } from '../api/client';

const AuthContext = createContext();

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (!context) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
};

export const AuthProvider = ({ children }) => {
    const [user, setUser] = useState(null);
    const [loading, setLoading] = useState(true);
    const [setupComplete, setSetupComplete] = useState(false); // Default to false for first boot safety
    const [error, setError] = useState(null);

    const checkAuth = async () => {
        let attempts = 0;
        const maxAttempts = 10;
        const delay = 1000; // 1 second

        while (attempts < maxAttempts) {
            try {
                // Check setup status first
                const setupData = await fetchSetupStatus();
                setSetupComplete(setupData.setup_complete);

                // If setup is complete, try to fetch user
                if (setupData.setup_complete) {
                    try {
                        const userData = await fetchCurrentUser();
                        setUser(userData);
                    } catch (err) {
                        // Token might be invalid or expired, but setup is complete
                        // Just clear user state
                        setUser(null);
                    }
                }

                // Success!
                setLoading(false);
                return;
            } catch (err) {
                attempts++;
                console.warn(`Auth check attempt ${attempts}/${maxAttempts} failed:`, err);

                if (attempts < maxAttempts) {
                    await new Promise(resolve => setTimeout(resolve, delay));
                } else {
                    console.error('All auth check attempts failed.');
                    setError('Unable to connect to the AstroCat server. Please ensure the backend container is running.');
                    // Do NOT force setupComplete to false here as default is false.
                    // The App component should handle the error state.
                    setUser(null);
                    setLoading(false);
                }
            }
        }
    };

    useEffect(() => {
        checkAuth();

        // Listen for unauthorized events from the API client
        const handleUnauthorized = () => {
            setUser(null);
        };

        window.addEventListener('api-unauthorized', handleUnauthorized);
        return () => window.removeEventListener('api-unauthorized', handleUnauthorized);
    }, []);

    const login = async (email, password) => {
        setError(null);
        try {
            const userData = await loginUser(email, password);
            setUser(userData);
            return userData;
        } catch (err) {
            setError(err.message);
            throw err;
        }
    };

    const registerAdmin = async (email, password) => {
        setError(null);
        try {
            const userData = await signupAdmin(email, password);
            setUser(userData);
            setSetupComplete(true);
            return userData;
        } catch (err) {
            setError(err.message);
            throw err;
        }
    };

    const logout = async () => {
        try {
            await logoutUser();
        } catch (err) {
            console.error('Logout failed:', err);
        } finally {
            setUser(null);
        }
    };

    const value = {
        user,
        loading,
        setupComplete,
        setSetupComplete,
        error,
        login,
        registerAdmin,
        logout,
        isAuthenticated: !!user
    };

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
};
