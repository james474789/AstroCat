import React, { createContext, useContext, useState, useEffect } from 'react';
import { fetchCurrentUser, loginUser, logoutUser, fetchSetupStatus } from '../api/client';

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
    const [setupComplete, setSetupComplete] = useState(true); // Default to true to avoid flicker
    const [error, setError] = useState(null);

    const checkAuth = async () => {
        try {
            // Check setup status first
            const setupData = await fetchSetupStatus();
            setSetupComplete(setupData.setup_complete);

            if (setupData.setup_complete) {
                const userData = await fetchCurrentUser();
                setUser(userData);
            }
        } catch (err) {
            setUser(null);
        } finally {
            setLoading(false);
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
        logout,
        isAuthenticated: !!user
    };

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
};
